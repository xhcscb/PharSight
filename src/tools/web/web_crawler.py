"""
Web Crawler APIs

This module contains web crawling and content extraction functionality.
"""

import asyncio
import html
import json
import re
from typing import Dict, List
from urllib.parse import urljoin

import aiohttp
import chardet
import httpx
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from ..base import Tool, ToolResult
from ..document.pdf_ingestion import download_and_parse_pdf, is_probable_pdf_url, remote_url_is_pdf


def _expected_document_type(title: str) -> str | None:
    compact = re.sub(r"\s+", "", title)
    if "年度报告摘要" in compact:
        return "annual_report_summary"
    if "年度报告" in compact and not any(marker in compact for marker in ("业绩推介", "业绩演示")):
        return "annual_report"
    if any(marker in compact for marker in ("业绩推介", "业绩说明会", "业绩演示", "全年业绩")):
        return "earnings_presentation"
    return None


def extract_pdf_links_from_markdown(content: str, base_url: str, parent_title: str = "") -> list[dict]:
    """Extract and normalize PDF attachments found in crawled Markdown."""
    candidates: list[tuple[str, str]] = []
    markdown_pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    for label, raw_target in markdown_pattern.findall(content):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>\"'")
        candidates.append((label.strip(), target))
    for raw_url in re.findall(r"https?://[^\s<>\]\[()\"']+", content):
        candidates.append(("", raw_url.rstrip(".,;，。；")))

    discovered: list[dict] = []
    seen: set[str] = set()
    for label, target in candidates:
        resolved = html.unescape(urljoin(base_url, target))
        if resolved in seen or not is_probable_pdf_url(resolved, {"title": label}):
            continue
        seen.add(resolved)
        combined_title = " - ".join(part for part in (parent_title.strip(), label) if part)
        title = combined_title or label or "PDF 附件"
        discovered.append(
            {
                "url": resolved,
                "title": title,
                "description": f"从 {base_url} 发现的 PDF 附件",
                "parent_url": base_url,
                "document_type": _expected_document_type(title),
            }
        )
    return discovered


class Click(Tool):
    """
    Web-page detail retrieval tool that fetches HTML or PDF content for review.
    """

    def __init__(self):
        super().__init__(
            name="Web page content fetcher",
            description="Retrieve detailed content for the supplied URLs (HTML or PDF) to support downstream analysis.",
            parameters=[
                {"name": "urls", "type": "List[str]", "description": "List of URLs to crawl", "required": True},
                {"name": "task", "type": "str", "description": "Overall task description used for filtering/summarization", "required": True}
            ],
        )
        self.type = 'tool_click'

    async def fetch_url(self, url: str) -> str:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status != 200:
                        return f"Error fetching url: HTTP status code {response.status}"

                    # Decode with detected encoding to avoid UTF-8 decode errors
                    raw_bytes = await response.read()
                    if not raw_bytes:
                        return "Error fetching url: Empty response"
                    detected_encoding = response.charset or chardet.detect(raw_bytes).get('encoding') or 'utf-8'
                    html_content = raw_bytes.decode(detected_encoding, errors='replace')

            soup = BeautifulSoup(html_content, 'html.parser')

            for element in soup(['script', 'style', 'meta', 'noscript', 'head', 'title']):
                element.extract()
            text = soup.get_text(separator=' ')

            lines = (line.strip() for line in text.splitlines())
            clean_text = '\n'.join(line for line in lines if line)

            return clean_text

        except asyncio.TimeoutError:
            return "Error fetching url: Request timeout"
        except Exception as e:
            return f"Error fetching url: {str(e)}"

    async def api_function(
        self,
        urls: List[str],
        task: str,
        url_metadata: Dict[str, Dict] | None = None,
    ) -> List[ToolResult]:
        """
        Crawl each URL and return the retrieved content (up to 10,000 chars).

        Args:
            urls: List of target URLs.
            task: Task description for future filtering (currently unused).

        Returns:
            List[ToolResult]: Collected page snippets.
        """
        if isinstance(urls, str):
            urls = [urls]
        try:
            browser_conf = BrowserConfig(headless=True)  # or False to see the browser
            run_conf = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS
            )
            result_list = []
            for url in urls:
                metadata = (url_metadata or {}).get(url, {})
                probable_pdf = is_probable_pdf_url(url, metadata)
                if not probable_pdf:
                    try:
                        probable_pdf = await remote_url_is_pdf(url)
                    except (ValueError, httpx.HTTPError):
                        probable_pdf = False
                if probable_pdf:
                    parsed_pdf = await download_and_parse_pdf(url)
                    content = parsed_pdf["content"]
                    expected_type = metadata.get("document_type")
                    actual_type = parsed_pdf.get("document_type")
                    if expected_type and actual_type != expected_type:
                        raise ValueError(
                            f"PDF identity mismatch: expected {expected_type}, detected {actual_type}"
                        )
                    result_list.append(
                        ClickResult(
                            name=(metadata.get("title") or content[:30] or "PDF document"),
                            description=f"PDF: {url}",
                            data=content[:120000],
                            link=url,
                            source=f"URL: {url}",
                            content_type=parsed_pdf["content_type"],
                            parser=parsed_pdf["parser"],
                            document_hash=parsed_pdf["document_hash"],
                            retrieved_at=parsed_pdf["retrieved_at"],
                            page_count=parsed_pdf["page_count"],
                            final_url=parsed_pdf["final_url"],
                            byte_size=parsed_pdf["byte_size"],
                            document_type=actual_type,
                            reporting_period=parsed_pdf.get("reporting_period"),
                            detected_title=parsed_pdf.get("detected_title"),
                        )
                    )
                    continue
                else:
                    async with AsyncWebCrawler(config=browser_conf) as crawler:
                        result = await crawler.arun(url=url, config=run_conf)
                        content = str(result.markdown)

                    parent_title = metadata.get("title") or content.splitlines()[0][:160]
                    discovered_links = extract_pdf_links_from_markdown(content, url, parent_title)

                    # use naive requests with async to get the content
                    # content = await self.fetch_url(url)

                # if task == '' or len(content) < 10000:
                result_list.append(
                    ClickResult(
                        name=content[:30],
                        description=f"Title: {url}",
                        data=content[:6000],
                        link=url,
                        source=f"URL: {url}",
                        discovered_links=discovered_links,
                    )
                )
                # else:
                #     # Use an LLM to extract the task-relevant snippets
                #     response = llm.generate(
                #         messages=[
                #             {"role": "system", "content": CONTENT_SUMMARY_PROMPT},
                #             {"role": "user", "content": f"Extract information related to {task} from the following page:\n{content[:10000]}"}
                #         ]
                #     )

                #     summary_json = self.extract_json_from_text(response)
                #     if summary_json and summary_json.get('title') and summary_json.get('content'):
                #         result_list.append(
                #             ClickResult(
                #                 name=f"Detailed information for {url}",
                #                 description=f"Title：{summary_json['title']}",
                #                 data=summary_json['content'],
                #                 source=f"URL: {url}"
                #             )
                #         )
            return result_list

        except Exception as e:
            print(f"Error: {e}")
            return []


    def extract_json_from_text(self, text: str) -> Dict:
        """
        Attempt to parse a JSON object embedded in the supplied text.

        Args:
            text: Text that may contain JSON.

        Returns:
            Dict: Parsed JSON object, or None on failure.
        """
        # First attempt to match fenced ```json``` blocks
        pattern = r'```json(.*?)```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception as e:
                print(f"Error parsing JSON from code block: {e}")
                return None
        else:
            # Fallback: parse the entire string as JSON
            try:
                return json.loads(text)
            except Exception as e:
                print(f"Error parsing JSON directly: {e}")
                return None

class ClickResult(ToolResult):
    """Container for web-content retrieval outputs."""

    def __init__(
        self,
        name,
        description,
        data,
        link="",
        source="",
        content_type="text/html",
        parser="crawl4ai",
        document_hash=None,
        retrieved_at=None,
        page_count=None,
        final_url=None,
        byte_size=None,
        document_type=None,
        reporting_period=None,
        detected_title=None,
        discovered_links=None,
    ):
        super().__init__(name, description, data, source)
        self.link = link
        self.content_type = content_type
        self.parser = parser
        self.document_hash = document_hash
        self.retrieved_at = retrieved_at
        self.page_count = page_count
        self.final_url = final_url or link
        self.byte_size = byte_size
        self.document_type = document_type
        self.reporting_period = reporting_period
        self.detected_title = detected_title
        self.discovered_links = discovered_links or []

    def __str__(self):
        format_output = self.name + "\n" + self.description + "\n\n"
        format_output += f"Content: {self.data}"
        return format_output

    def brief_str(self):
        format_output = self.name + "\n" + self.description + "\n\n"
        format_output += f"Content: {self.data[:100]}"
        return format_output

    def __repr__(self):
        return self.__str__()
