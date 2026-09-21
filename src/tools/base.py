import json
import uuid

import pandas as pd


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
        """Return a stable evidence ID when the payload provides one."""
        if isinstance(self.data, dict):
            value = self.data.get("source_id")
            return str(value).strip().upper() if value else None
        return None

    def brief_str(self):
        if self.source_id:
            metadata = {
                key: self.data.get(key)
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
                if self.data.get(key) is not None
            }
            content = str(self.data.get("content", ""))[:3000]
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
        title = self.data.get("title") or self.name
        publisher = self.data.get("publisher") or "未知发布者"
        published_at = self.data.get("published_at") or "日期未知"
        locator = self.data.get("locator")
        location = (
            self.data.get("url")
            or self.data.get("external_id")
            or self.data.get("local_path")
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
