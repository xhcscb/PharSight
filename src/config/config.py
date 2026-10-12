import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

PHARMA_TARGET_TYPES = {"pharma_company", "pharma_industry"}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|authorization|cookie|bearer)",
    re.IGNORECASE,
)


class Config:
    def __init__(self, config_file_path=None, config_dict=None):
        # load default config
        current_path = os.path.dirname(os.path.realpath(__file__))
        default_file_path = os.path.join(current_path, "default_config.yaml")
        self.config = self._load_config(default_file_path)
        self.project_root = Path(current_path).resolve().parents[1]

        # load from file
        self.config_file_path = config_file_path
        if config_file_path is not None:
            file_config = self._load_config(config_file_path)
            self.config.update(file_config)

        # load from dict
        self.config.update(config_dict or {})
        self.config = self._replace_env_vars(self.config)

        self._validate_live_search_config()
        self._validate_research_config()
        self._normalize_project_paths()
        self._set_dirs()
        self._set_llms()
        self._set_rate_limiter()


    def _load_config(self, config_file_path):
        config_file_path = os.fspath(config_file_path)

        def build_yaml_loader():
            loader = yaml.FullLoader
            loader.add_implicit_resolver(
                "tag:yaml.org,2002:float",
                re.compile(
                    """^(?:
                [-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                |\\.[0-9_]+(?:[eE][-+][0-9]+)?
                |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*
                |[-+]?\\.(?:inf|Inf|INF)
                |\\.(?:nan|NaN|NAN))$""",
                    re.X,
                ),
                list("-+0123456789."),
            )
            return loader

        yaml_loader = build_yaml_loader()
        file_config = dict()
        if os.path.exists(config_file_path):
            if config_file_path.endswith('.yaml'):
                with open(config_file_path, "r", encoding="utf-8") as f:
                    file_config.update(yaml.load(f.read(), Loader=yaml_loader))
            elif config_file_path.endswith('.json'):
                with open(config_file_path, 'r') as f:
                    file_config.update(json.load(f))
            else:
                raise ValueError(f"Unsupported file type: {config_file_path}")
        else:
            raise ValueError(f"Config file not found: {config_file_path}")

        return file_config

    @staticmethod
    def _replace_env_vars(obj):
        """Replace ``${VAR_NAME}`` after all configuration layers are merged."""
        if isinstance(obj, dict):
            return {key: Config._replace_env_vars(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [Config._replace_env_vars(item) for item in obj]
        if not isinstance(obj, str):
            return obj

        result = obj
        for var_name in re.findall(r'\$\{([^}]+)\}', obj):
            env_value = os.getenv(var_name)
            if env_value is None:
                raise ValueError(f"Environment variable '{var_name}' is not set")
            result = result.replace(f"${{{var_name}}}", env_value)
        return result



    def _validate_research_config(self):
        """Validate the additional contract used by pharmaceutical modes."""
        target_type = self.config.get("target_type", "general")
        if target_type not in PHARMA_TARGET_TYPES:
            return

        required = ["target_name", "as_of", "jurisdictions", "research_questions"]
        if target_type == "pharma_company":
            required.append("stock_code")
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise ValueError(
                f"{target_type} configuration is missing required fields: "
                + ", ".join(missing)
            )

        as_of = str(self.config["as_of"])
        try:
            parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "'as_of' must be an ISO-8601 timestamp, for example "
                "2026-09-21T18:00:00+08:00"
            ) from exc
        if parsed_as_of.tzinfo is None:
            raise ValueError("'as_of' must include an explicit timezone")

        for control in ("strict_cutoff", "strict_citations", "require_citations"):
            if self.config.get(control, True) is not True:
                raise ValueError(f"'{control}' must be true for pharmaceutical modes")

        for key in ("jurisdictions", "research_questions"):
            value = self.config[key]
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ValueError(f"'{key}' must be a non-empty list of strings")

        for key in ("custom_collect_tasks", "custom_analysis_tasks"):
            value = self.config.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"'{key}' must be a list of strings")

        output_formats = self.config.get("output_formats", ["md"])
        if not isinstance(output_formats, list) or not output_formats:
            raise ValueError("'output_formats' must be a non-empty list")
        unsupported = set(output_formats) - {"md", "docx", "pdf"}
        if unsupported:
            raise ValueError(
                "Unsupported output formats: " + ", ".join(sorted(unsupported))
            )

        selection_mode = str(self.config.get("evidence_selection", "auto")).strip().lower()
        if selection_mode not in {"auto", "off", "llm"}:
            raise ValueError("'evidence_selection' must be one of: auto, off, llm")
        for key in ("evidence_selection_threshold", "evidence_selection_max_k"):
            value = self.config.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, bool):
                raise ValueError(f"'{key}' must be a positive integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"'{key}' must be a positive integer") from exc
            if parsed < 1:
                raise ValueError(f"'{key}' must be a positive integer")

        retrieval_backend = str(self.config.get("retrieval_backend", "chunk")).strip().lower()
        if retrieval_backend not in {"chunk", "graph", "hybrid"}:
            raise ValueError("'retrieval_backend' must be one of: chunk, graph, hybrid")
        self.config["retrieval_backend"] = retrieval_backend
        if not isinstance(self.config.get("graph_build_on_ingest", True), bool):
            raise ValueError("'graph_build_on_ingest' must be a boolean")
        for key, minimum, maximum, default in (
            ("graph_max_hops", 0, 2, 2),
            ("graph_top_k", 1, 30, 8),
            ("graph_query_planning_timeout", 1, 300, 30),
            ("graph_build_concurrency", 1, 16, 4),
            ("graph_json_max_tokens", 4096, 32768, 16384),
            ("graph_relation_candidate_limit", 1, 10000, 100),
        ):
            value = self.config.get(key, default)
            if isinstance(value, bool):
                raise ValueError(f"'{key}' must be an integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"'{key}' must be an integer") from exc
            if parsed < minimum or parsed > maximum:
                raise ValueError(f"'{key}' must be between {minimum} and {maximum}")
            self.config[key] = parsed
        try:
            threshold = float(self.config.get("graph_relation_confidence_threshold", 0.7))
        except (TypeError, ValueError) as exc:
            raise ValueError("'graph_relation_confidence_threshold' must be numeric") from exc
        if threshold < 0 or threshold > 1:
            raise ValueError("'graph_relation_confidence_threshold' must be between 0 and 1")
        self.config["graph_relation_confidence_threshold"] = threshold
        try:
            failure_ratio = float(self.config.get("graph_max_extraction_failure_ratio", 0.1))
        except (TypeError, ValueError) as exc:
            raise ValueError("'graph_max_extraction_failure_ratio' must be numeric") from exc
        if failure_ratio < 0 or failure_ratio > 1:
            raise ValueError("'graph_max_extraction_failure_ratio' must be between 0 and 1")
        self.config["graph_max_extraction_failure_ratio"] = failure_ratio

    def _validate_live_search_config(self):
        """Validate the explicitly enabled live-search provider."""
        if not self.config.get("live_web_search", False):
            return
        provider = str(self.config.get("search_provider", "serper")).strip().lower()
        if provider != "serper":
            raise ValueError(
                f"Unsupported search_provider '{provider}'. Currently supported: serper"
            )
        self.config["search_provider"] = provider
        if not os.getenv("SERPER_API_KEY"):
            raise ValueError(
                "SERPER_API_KEY is required when live_web_search uses Serper"
            )

    def _normalize_project_paths(self):
        """Resolve project-owned paths relative to the Pharsight repository."""
        for key in (
            "output_dir",
            "reference_doc_path",
            "outline_template_path",
            "source_manifest",
            "graph_store_path",
        ):
            raw_value = self.config.get(key)
            if not raw_value:
                continue
            path = Path(raw_value)
            if not path.is_absolute():
                path = self.project_root / path
            self.config[key] = str(path.resolve())

    @staticmethod
    def redacted_copy(value):
        """Return a recursively redacted copy suitable for logs and snapshots."""
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if SENSITIVE_KEY_PATTERN.search(str(key)):
                    redacted[key] = "***REDACTED***"
                else:
                    redacted[key] = Config.redacted_copy(item)
            return redacted
        if isinstance(value, list):
            return [Config.redacted_copy(item) for item in value]
        return deepcopy(value)

    def _set_dirs(self):
        output_dir = Path(self.config.get("output_dir", self.project_root / "outputs")).resolve()
        try:
            output_dir.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(
                f"output_dir must stay inside the Pharsight repository: {output_dir}"
            ) from exc
        self.config['output_dir'] = str(output_dir)
        target = str(self.config.get('target_name', 'unknown'))
        save_note = self.config.get('save_note', None)
        if save_note:
            target = str(save_note) + '_' + target
        target = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', target)[:80].strip(' .')
        target = target or 'unknown'
        working_dir = (output_dir / target).resolve()
        try:
            working_dir.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(
                f"working_dir must stay inside the Pharsight repository: {working_dir}"
            ) from exc
        self.working_dir = str(working_dir)
        self.config['working_dir'] = self.working_dir
        os.makedirs(self.working_dir, exist_ok=True)
        with open(os.path.join(self.working_dir, 'config.json'), 'w', encoding='utf-8') as f:
            json.dump(self.redacted_copy(self.config), f, indent=4, ensure_ascii=False)


    def _set_llms(self):
        try:
            from src.utils.llm import AsyncLLM
        except ModuleNotFoundError as exc:
            if exc.name == "openai":
                raise RuntimeError(
                    "The 'openai' package is required to configure language models. "
                    "Install the Pharsight project dependencies first."
                ) from exc
            raise
        llm_config_list = self.config.get('llm_config_list', [])
        llm_dict = {}
        for llm_config in llm_config_list:
            model_name = llm_config['model_name']
            llm_dict[model_name] = self.create_llm(model_name, llm_class=AsyncLLM)
        self.llm_dict = llm_dict

    def create_llm(self, model_name: str, *, llm_class=None):
        """Create an isolated async client for one agent or memory operation."""
        if llm_class is None:
            from src.utils.llm import AsyncLLM

            llm_class = AsyncLLM
        llm_config = next(
            (
                item
                for item in self.config.get('llm_config_list', [])
                if item.get('model_name') == model_name
            ),
            None,
        )
        if llm_config is None:
            raise ValueError(f"Model '{model_name}' is not present in llm_config_list")
        return llm_class(
            base_url=llm_config['base_url'],
            api_key=llm_config['api_key'],
            model_name=model_name,
            generation_params=llm_config.get('generation_params', {}),
        )

    def _set_rate_limiter(self):
        """Initialize the global rate limiter from config."""
        from src.utils.rate_limiter import RateLimiter
        rate_limits = self.config.get('rate_limits', {})
        self.rate_limiter = RateLimiter(rate_limits)

    def __str__(self):
        return str(self.config)
