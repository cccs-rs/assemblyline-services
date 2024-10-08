import hashlib
import json
import os
import tempfile
from collections import defaultdict
from copy import copy
from threading import RLock
from typing import Iterable

import yaml
from assemblyline.common.exceptions import RecoverableError
from assemblyline_v4_service.common.base import ServiceBase
from assemblyline_v4_service.common.request import ServiceRequest
from assemblyline_v4_service.common.result import (
    Result,
    ResultMemoryDumpSection,
    ResultMultiSection,
    ResultTextSection,
)

from .controller import (
    AL_TO_SG_LANGUAGE,
    LANGUAGE_TO_EXT,
    ASTGrepDeobfuscationController,
    ASTGrepLSPController,
    ASTGrepScanController,
    UnsupportedLanguageError,
)
from .helpers import configure_yaml

configure_yaml()

# RULES_DIR = os.path.join(UPDATES_DIR, "sg_rules")

EXT_TO_FILE_TYPE = {ext[1:]: type_ for type_, ext in LANGUAGE_TO_EXT.items()}
EXT_TO_FILE_TYPE["js"] = "code/javascript"

SEVERITY_TO_HEURISTIC = {
    "INFO": 3,
    "WARNING": 1,
    "ERROR": 2,
    "LOW": 3,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 2,
    # LSP severities
    "1": 2,
    "2": 1,
    "3": 3,
    "4": 3,
}

RULES_LOCK = RLock()
USE_LSP = True
MAX_LINE_SIZE = 5000

RULES_DIR = [
    "./rules",
    # "./rules/detection",
]


class AssemblylineService(ServiceBase):
    def _read_rules(self):
        for rule_path in RULES_DIR:
            for root, _, files in os.walk(rule_path):
                for file in files:
                    if (
                        file.endswith(".yml") or file.endswith(".yaml")
                    ) and ".sgconfig." not in file:
                        with open(os.path.join(root, file), "r") as f:
                            yaml_docs = yaml.safe_load_all(f)

                            for yaml_doc in yaml_docs:
                                if not yaml_doc:
                                    continue
                                metadata = yaml_doc.get("metadata", {})
                                self.metadata_cache[yaml_doc.get("id")] = metadata
                                self._sg_languages_to_scan.add(yaml_doc.get("language"))

    def __init__(self, config=None):
        super().__init__(config)
        self._active_rules_dir = None
        self._active_deobfuscation_rules_dir = None
        self.metadata_cache = {}
        self._sg_languages_to_scan = set()

        self.use_lsp = self.config.get("USE_LANGUAGE_SERVER_PROTOCOL", True)
        self.extract_intermediate_layers = self.config.get("EXTRACT_INTERMEDIATE_LAYERS", False)
        self.try_language_from_extension = self.config.get("TRY_LANGUAGE_FROM_EXTENSION", True)
        if self.use_lsp:
            self._astgrep = ASTGrepLSPController(self.log, RULES_DIR)
        else:
            self._astgrep = ASTGrepScanController(self.log, RULES_DIR)
        self._deobfuscator = ASTGrepDeobfuscationController(self.log, RULES_DIR)
        self._read_rules()

    def start(self):
        self.log.info(f"{self.service_attributes.name} service started")

    # def _load_rules(self) -> None:
    #     # Currently just a stub for AST-Grep
    #     return

    def _get_code_hash(self, code: str):
        code = code or ""
        # re-arrange code in one line to increase hash consistency
        code = "".join(line.strip() for line in code.split("\n"))
        if not code:
            return ""
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        return f"code.{code_hash}"

    def _read_lines(self, lines_no: set[tuple[int, int]]):
        lines = defaultdict(list)
        slices_by_start = defaultdict(list)
        for start, end in lines_no:
            slices_by_start[start].append(end)

        open_slices = list()
        with open(self._request.file_path, "r") as f:
            for i, line in enumerate(f):
                if i in slices_by_start:
                    for end in slices_by_start[i]:
                        open_slices.append((i, end))
                for slice_ in copy(open_slices):
                    self.log.debug(f"Reading line {i} for slice {slice_}, {type(slice_)}")
                    lines[slice_].append(line)
                    if i == slice_[1]:
                        open_slices.remove(slice_)
                if not open_slices and len(lines) == len(lines_no):
                    break
        return {k: "".join(v) for k, v in lines.items()}

    def _process_results(self, results: list[dict]) -> Iterable[ResultMultiSection]:
        result_by_rule = defaultdict(list)
        lines_by_rule = defaultdict(set)
        line_no = set()
        for result in results:
            line_start, line_end = result["start"]["line"], result["end"]["line"]
            if (line_start, line_end) not in lines_by_rule[result["check_id"]]:
                line_no.add((line_start, line_end))
                result_by_rule[result["check_id"]].append(result)
                lines_by_rule[result["check_id"]].add((line_start, line_end))

        lines = dict()
        if self.use_lsp and line_no:
            lines = self._read_lines(line_no)

        self._should_deobfuscate = False

        for rule_id, matches in result_by_rule.items():
            extra = matches[0].get("extra", {})
            message = extra.get("message", "").replace("\n\n", "\n")
            severity = extra.get("severity", "INFO")
            heuristic = SEVERITY_TO_HEURISTIC.get(str(severity).upper(), 0)

            # TODO: Support for attribution
            metadata = self.metadata_cache.get(rule_id, {})
            title = metadata.get("title", metadata.get("name", message[:100]))
            attack_id = metadata.get("attack_id")

            is_deobfuscation = metadata.get("extended-obfuscation", False)
            self._should_deobfuscate = self._should_deobfuscate or is_deobfuscation

            section = ResultTextSection(
                title,
                zeroize_on_tag_safe=True,
            )
            section.add_line(message)
            section.set_heuristic(heuristic, signature=rule_id, attack_id=attack_id)
            for match in matches:
                line_start, line_end = match["start"]["line"], match["end"]["line"]
                line = match["extra"].get("lines", lines.get((line_start, line_end), ""))
                code_hash = self._get_code_hash(line)
                title = f"Match at lines {line_start} - {line_end}"
                if line_start == line_end:
                    title = f"Match at line {line_start}"
                ResultMemoryDumpSection(
                    title,
                    body=line[:MAX_LINE_SIZE],
                    parent=section,
                    zeroize_on_tag_safe=True,
                    tags={"file.rule.astgrep": [code_hash, rule_id]},
                )
                section.add_tag("file.rule.astgrep", code_hash)
                # Looks like heuristic in subsections causes zeroization to fail
                # subsection.set_heuristic(heuristic, signature=rule_id, attack_id=attack_id)
            yield section

    def _process_file(self, request: ServiceRequest, file_path, file_type):
        main_section = ResultTextSection(f"Processing as {file_type}")
        try:
            results = self._astgrep.process_file(file_path, file_type)
            request.set_service_context(self._astgrep.version)
            for result_section in self._process_results(results):
                main_section.add_subsection(result_section)
        except UnsupportedLanguageError:
            self.log.warning(f"Unsupported language: {file_type}")
            return

        if self._astgrep.last_results:
            with tempfile.NamedTemporaryFile("w", delete=False) as f:
                json.dump(self._astgrep.last_results, f, indent=2)
            request.add_supplementary(f.name, "astgrep_raw_results.json", "AST-Grep Results")

        if self._should_deobfuscate:
            extracted_layers = []
            result_no = 1
            for deobf_result, layer in self._deobfuscator.deobfuscate_file(file_path, file_type):
                path = f"{self.working_directory}/_deobfuscated_code_{result_no}{LANGUAGE_TO_EXT[file_type]}"
                with open(path, "w+") as f:
                    f.write(deobf_result)
                if layer != "#final-layer#":
                    extracted_layers.append(
                        (
                            path,
                            f"_deobfuscated_code_{result_no}{LANGUAGE_TO_EXT[file_type]}",
                            f"Deobfuscated code extracted by {layer}",
                        )
                    )
                else:
                    request.add_extracted(
                        path,
                        f"_deobfuscated_code_FINAL{LANGUAGE_TO_EXT[file_type]}",
                        "Final deobfuscation layer",
                    )
                result_no += 1
            deobf_section = ResultTextSection(
                "Obfuscation found"
                if self._deobfuscator.confirmed_obfuscation
                else "Possible obfuscation"
            )
            deobf_section.add_line(self._deobfuscator.status)
            deobf_section.set_heuristic(4 if self._deobfuscator.confirmed_obfuscation else 5)
            main_section.add_subsection(deobf_section)

            if extracted_layers:
                for args in extracted_layers[:-10:-1]:
                    if self.extract_intermediate_layers:
                        request.add_extracted(*args)
                    else:
                        request.add_supplementary(*args)
            if len(extracted_layers) > 10:
                layers_section = ResultTextSection(
                    f"Found {len(extracted_layers)} layers of obfuscation"
                )
                layers_section.add_line(
                    "Only last 10 extracted layers and the final layer are shown"
                )
                layers_section.set_heuristic(6)
                main_section.add_subsection(layers_section)

        if main_section.subsections:
            request.result.add_section(main_section)

    def _should_scan_type(self, al_file_type: str) -> bool:
        try:
            sg_lang = AL_TO_SG_LANGUAGE.get(al_file_type.split("/")[1].lower())
        except (KeyError, IndexError):
            return False

        return sg_lang in self._sg_languages_to_scan

    def execute(self, request: ServiceRequest) -> None:
        if not self._astgrep or not self._astgrep.ready:
            raise RecoverableError("AST-Grep isn't ready yet")

        self._request = request
        result = Result()
        request.result = result

        if self._should_scan_type(request.file_type):
            self._process_file(request, request.file_path, request.file_type)

        if not self.try_language_from_extension:
            return

        file_ext = request.file_name.split(".")[-1]
        try:
            file_type = EXT_TO_FILE_TYPE[file_ext]
            self.log.debug(f"File type from extension: {file_type}")
        except KeyError:
            return

        # TODO: javascript vs jscript
        if file_type != request.file_type and self._should_scan_type(file_type):
            if all(
                type_ in ["code/javascript", "code/jscript"]
                for type_ in (request.file_type, file_type)
            ):
                return
            self._process_file(request, request.file_path, file_type)

    def _cleanup(self) -> None:
        self._astgrep.cleanup()
        super()._cleanup()

    def stop(self) -> None:
        self._astgrep.stop()
