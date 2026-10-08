import json
import re
import uuid

import pandas as pd

SOURCE_ID_PREFIX_PATTERN = re.compile(r"^([A-Z][A-Z0-9_-]{0,31})\b")


def _source_id_from_name(name) -> str | None:
    """Parse a stable source ID from a collector result name such as ``S001 年报``.

    Requires at least one digit so ordinary English words (for example ``OK``)
    are not mistaken for evidence IDs.
    """
    match = SOURCE_ID_PREFIX_PATTERN.match(str(name or "").strip().upper())
    if not match:
        return None
    candidate = match.group(1)
    if len(candidate) < 2 or not any(char.isdigit() for char in candidate):
        return None
    return candidate


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: list[dict]
    ):
        self.name = name
        self.type = f'tool_{name}'
        self.id = f"tool_{name}_{uuid.uuid4().hex[:8]}"
        self.short_description = description
        self.parameters = parameters

    def prepare_params(self, task) -> dict:
        """
        Optional hook to derive API parameters from a task payload.
        """
        return {}

    @property
    def description(self):
        params_str = ", ".join([
            f"{p['name']}: {p['type']} ({p['description']})"
                for p in self.parameters
        ])
        return f"Tool name: {self.name}\nDescription: {self.short_description}\nParameters: {params_str}\n"

    async def api_function(self, **kwargs):
        """
        Execute the underlying API and return structured data.
        """
        raise NotImplementedError

    async def get_data(self, task):
        params = self.prepare_params(task)
        try:
            data = await self.api_function(**params)
            task.all_results.extend(data)
            return data
        except Exception as e:
            print(f"Error: {e}")
            return []


class ToolResult:
    def __init__(self, name, description, data, source = ""):
        self.name = name
        self.description = description
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        self.data = data
        self.data_type = type(data)
        self.source = source  # str, data source

    @property
    def source_id(self):
        """Return a stable evidence ID from the payload or the result name.

        Collectors save results as ``S001 ...``; accepting that stable prefix as
        the source ID keeps those records citable without rewriting their data.
        """
        if isinstance(self.data, dict):
            value = self.data.get("source_id")
            if value:
                return str(value).strip().upper()
        return _source_id_from_name(self.name)

    @staticmethod
    def _evidence_payload(data) -> dict:
        return data if isinstance(data, dict) else {}

    def brief_str(self):
        payload = self._evidence_payload(self.data)
        if self.source_id:
            metadata = {
                key: payload.get(key)
                for key in (
                    "source_id",
                    "source_type",
                    "publisher",
                    "title",
                    "published_at",
                    "updated_at",
                    "reporting_period",
                    "locator",
                    "url",
                    "external_id",
                    "reliability_tier",
                    "evidence_label",
                    "cutoff_eligible",
                    "entity_keys",
                    "limitations",
                )
                if payload.get(key) is not None
            }
            metadata.setdefault("source_id", self.source_id)
            metadata.setdefault("title", self.name)
            content = str(payload.get("content", self.data))[:3000]
            return (
                "Evidence metadata:\n"
                + json.dumps(metadata, ensure_ascii=False, indent=2)
                + f"\nEvidence content preview:\n{content}"
            )
        return self.__str__()

    def reference_text(self):
        """Render an evidence record for a report source register."""
        if not self.source_id:
            return str(self.source)
        payload = self._evidence_payload(self.data)
        title = payload.get("title") or self.name
        publisher = payload.get("publisher") or "未知发布者"
        published_at = payload.get("published_at") or "日期未知"
        locator = payload.get("locator")
        location = (
            payload.get("url")
            or payload.get("external_id")
            or payload.get("local_path")
            or self.source
        )
        parts = [f"{self.source_id}. {publisher}：《{title}》", str(published_at)]
        if locator:
            parts.append(str(locator))
        if location:
            parts.append(str(location))
        return "；".join(parts)

    def get_full_string(self):
        if isinstance(self.data, pd.DataFrame):
            return self.data.to_string()
        else:
            return str(self.data)

    def __str__(self):
        base_string = f"Data name: {self.name}\nDescription: {self.description}\nSource: {self.source}\n"
        base_string += f"Data type: {type(self.data)}\n"
        if isinstance(self.data, pd.DataFrame):
            format_string = ""
            format_string += f"First five rows:\n{self.data.head().to_string()}\n"
        elif isinstance(self.data, dict):
            format_string = "Partial data preview: "
            format_string += str(self.data)[:100]
        elif isinstance(self.data, list):
            format_string = "Partial data preview: "
            format_string += str(self.data)[:100]
        else:
            format_string = "Partial data preview: "
            format_string += str(self.data)[:100]

        return base_string + format_string

    def __repr__(self):
        return self.__str__()

    def __hash__(self):
        return hash(self.name+self.description)

    def __eq__(self, other):
        return self.name == other.name and self.description == other.description
