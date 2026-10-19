"""
Unified Multi-Format Data Ingestion and Output Exporter for Meesho E2E Engine.

Supports Input Formats:
- Single JSON file
- Folder / Directory of JSON files
- Merged JSON array file [ {account1}, {account2}, ... ]
- JSON Lines (.jsonl) file or raw text
- Raw JSON string

Supports Output Formats:
- Individual 1-by-1 .txt files grouped into category subfolders
- Consolidated JSON file(s) by category

Privacy Safeguards:
- Automatically blacklists operator's personal account (7977748514) from all ingestion and display.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Tuple

logger = logging.getLogger("DataIO")

# STRICT OPERATOR PRIVACY: Never expose or process the operator's personal account
BLACKLISTED_NUMBERS = {"7977748514"}
BLACKLISTED_PATTERNS = {"7977748514"}


def is_blacklisted_account(data: Any, source_path: str = "") -> bool:
    """Checks whether an account belongs to the blacklisted personal account."""
    if any(bp in str(source_path) for bp in BLACKLISTED_PATTERNS):
        return True

    if isinstance(data, dict):
        mob = str(data.get("mobile") or data.get("phone") or "").replace("+91", "").strip()
        if mob in BLACKLISTED_NUMBERS:
            return True
        uid = str(data.get("user_id") or "")
        if uid in BLACKLISTED_NUMBERS:
            return True

    return False


def extract_account_list_from_dict(d: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Unpacks embedded account lists from batch summaries or export wrappers:
    - d['summary']['refreshed']
    - d['refreshed']
    - d['accounts']
    - d['records']
    - d['data']
    - d['assignments']
    - d['items']
    """
    if not isinstance(d, dict):
        return None

    # Check summary wrapper
    summary = d.get("summary")
    if isinstance(summary, dict):
        for k in ("refreshed", "accounts", "records", "data", "scanned", "fresh_accounts", "used_accounts"):
            val = summary.get(k)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val

    # Check top-level keys
    for k in ("refreshed", "accounts", "records", "data", "assignments", "items", "detailed_results"):
        val = d.get(k)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val

    return None


def load_accounts_from_source(
    source: Union[str, Path, List[Any], Dict[str, Any]],
    filter_blacklisted: bool = True
) -> List[Dict[str, Any]]:
    """
    Ingests account records from any supported source format:
    - Path to a single .json file
    - Path to a directory containing .json files
    - Path to a .jsonl file
    - Raw JSON array string or JSONL text
    - Python list of dictionaries
    - Single Python dictionary

    Returns a list of standardized record dicts with:
    - 'user_id', 'mobile', 'xo'
    - 'source_reference'
    - 'raw_data'
    """
    records: List[Dict[str, Any]] = []

    # Case 1: Already a Python list (of dicts, Path objects, or filepath strings)
    if isinstance(source, list):
        for idx, item in enumerate(source):
            if isinstance(item, dict):
                inner_list = extract_account_list_from_dict(item)
                if inner_list is not None:
                    records.extend(load_accounts_from_source(inner_list, filter_blacklisted=filter_blacklisted))
                    continue
                src_ref = item.get("source_reference") or f"list_item_{idx}"
                if filter_blacklisted and is_blacklisted_account(item, src_ref):
                    continue
                rec = dict(item)
                rec["source_reference"] = src_ref
                records.append(rec)
            elif isinstance(item, (str, Path)):
                sub_records = load_accounts_from_source(item, filter_blacklisted=filter_blacklisted)
                records.extend(sub_records)
        return records

    # Case 2: Already a single Python dictionary
    if isinstance(source, dict):
        inner_list = extract_account_list_from_dict(source)
        if inner_list is not None:
            return load_accounts_from_source(inner_list, filter_blacklisted=filter_blacklisted)

        src_ref = source.get("source_reference") or "dict_object"
        if not (filter_blacklisted and is_blacklisted_account(source, src_ref)):
            rec = dict(source)
            rec["source_reference"] = src_ref
            records.append(rec)
        return records

    # Case 3: Source is a path or string
    src_str = str(source).strip()
    p = Path(src_str)

    # Sub-case 3a: Directory path
    if p.is_dir():
        for json_file in sorted(p.glob("*.json")):
            if filter_blacklisted and is_blacklisted_account({}, json_file.name):
                continue
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    content = json.load(f)
                if isinstance(content, list):
                    for idx, item in enumerate(content):
                        if isinstance(item, dict):
                            src_ref = f"{json_file.name}[{idx}]"
                            if filter_blacklisted and is_blacklisted_account(item, src_ref):
                                continue
                            item_copy = dict(item)
                            item_copy["source_reference"] = src_ref
                            records.append(item_copy)
                elif isinstance(content, dict):
                    if filter_blacklisted and is_blacklisted_account(content, json_file.name):
                        continue
                    item_copy = dict(content)
                    item_copy["source_reference"] = str(json_file)
                    records.append(item_copy)
            except Exception as e:
                logger.warning(f"Error loading {json_file}: {e}")
        return records

    # Sub-case 3b: Single File on disk
    if p.is_file():
        if filter_blacklisted and is_blacklisted_account({}, p.name):
            return []

        # Check if it's JSONL
        if p.suffix.lower() in (".jsonl", ".txt", ".lines"):
            with open(p, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        obj = json.loads(line_s)
                        if isinstance(obj, dict):
                            src_ref = f"{p.name}:line_{line_no}"
                            if filter_blacklisted and is_blacklisted_account(obj, src_ref):
                                continue
                            obj_copy = dict(obj)
                            obj_copy["source_reference"] = src_ref
                            records.append(obj_copy)
                    except Exception as e:
                        logger.warning(f"Error parsing line {line_no} in {p}: {e}")
            return records

        # Standard JSON file (object or array)
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, dict):
                inner_list = extract_account_list_from_dict(content)
                if inner_list is not None:
                    return load_accounts_from_source(inner_list, filter_blacklisted=filter_blacklisted)

            if isinstance(content, list):
                for idx, item in enumerate(content):
                    if isinstance(item, dict):
                        src_ref = f"{p.name}[{idx}]"
                        if filter_blacklisted and is_blacklisted_account(item, src_ref):
                            continue
                        item_copy = dict(item)
                        item_copy["source_reference"] = src_ref
                        records.append(item_copy)
            elif isinstance(content, dict):
                if not (filter_blacklisted and is_blacklisted_account(content, p.name)):
                    item_copy = dict(content)
                    item_copy["source_reference"] = str(p)
                    records.append(item_copy)
            return records
        except Exception as e:
            logger.warning(f"Error parsing JSON from {p}: {e}")

    # Sub-case 3c: In-memory string (JSON array, JSON object, or JSONL)
    if src_str.startswith("{") or src_str.startswith("["):
        try:
            parsed = json.loads(src_str)
            if isinstance(parsed, dict):
                inner_list = extract_account_list_from_dict(parsed)
                if inner_list is not None:
                    return load_accounts_from_source(inner_list, filter_blacklisted=filter_blacklisted)

            if isinstance(parsed, list):
                for idx, item in enumerate(parsed):
                    if isinstance(item, dict):
                        src_ref = f"inline_array[{idx}]"
                        if filter_blacklisted and is_blacklisted_account(item, src_ref):
                            continue
                        item_copy = dict(item)
                        item_copy["source_reference"] = src_ref
                        records.append(item_copy)
                return records
            elif isinstance(parsed, dict):
                if not (filter_blacklisted and is_blacklisted_account(parsed, "inline_json")):
                    parsed_copy = dict(parsed)
                    parsed_copy["source_reference"] = "inline_json"
                    records.append(parsed_copy)
                return records
        except Exception:
            pass

    # Try parsing string as JSONL lines
    for line_no, line in enumerate(src_str.splitlines(), 1):
        line_s = line.strip()
        if not line_s:
            continue
        try:
            obj = json.loads(line_s)
            if isinstance(obj, dict):
                src_ref = f"inline_line_{line_no}"
                if filter_blacklisted and is_blacklisted_account(obj, src_ref):
                    continue
                obj_copy = dict(obj)
                obj_copy["source_reference"] = src_ref
                records.append(obj_copy)
        except Exception:
            pass

    return records


def export_categorized_results(
    categorized_items: Dict[str, List[Dict[str, Any]]],
    output_dir: Union[str, Path],
    format_type: str = "combined_json",
    file_prefix: str = "account"
) -> Dict[str, Any]:
    """
    Exports classified/processed results to disk according to the user's choice:
    - format_type = 'individual_txt':
      Saves 1-by-1 .txt files per account inside category subfolders:
      output_dir/<category>/<account_id_or_mobile>.txt
    - format_type = 'combined_json':
      Saves a consolidated .json file per category:
      output_dir/<category>.json and a summary file output_dir/summary.json
    """
    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    written_files: List[str] = []
    category_counts: Dict[str, int] = {}

    for cat_name, items in categorized_items.items():
        clean_cat = str(cat_name).replace(" ", "_").replace("/", "_").lower()
        category_counts[clean_cat] = len(items)

        if format_type.lower() in ("individual_txt", "individual", "txt", "1by1"):
            # Subfolder per category
            cat_dir = out_path / clean_cat
            cat_dir.mkdir(parents=True, exist_ok=True)

            for idx, item in enumerate(items):
                # Identify account filename
                acc_id = str(item.get("account_id") or item.get("user_id") or item.get("mobile") or idx)
                # Sanitize filename
                acc_filename = f"{acc_id}.txt"
                target_file = cat_dir / acc_filename

                with open(target_file, "w", encoding="utf-8") as fp:
                    # Write formatted readable txt content
                    fp.write(f"Account Record: {acc_id}\n")
                    fp.write(f"Category: {clean_cat}\n")
                    fp.write("=" * 60 + "\n")
                    for k, v in item.items():
                        if k != "raw_data" and k != "raw_record":
                            fp.write(f"{k}: {v}\n")
                    fp.write("=" * 60 + "\n\nRaw JSON Data:\n")
                    raw = item.get("raw_data") or item.get("raw_record") or item
                    fp.write(json.dumps(raw, indent=2, ensure_ascii=False))

                written_files.append(str(target_file))

        else:
            # Combined JSON file per category
            target_json = out_path / f"{clean_cat}.json"
            with open(target_json, "w", encoding="utf-8") as fp:
                json.dump(items, fp, indent=2, ensure_ascii=False)
            written_files.append(str(target_json))

    # Always write a consolidated index / summary.json
    summary_file = out_path / "export_summary.json"
    summary_data = {
        "output_directory": str(out_path),
        "format_type": format_type,
        "total_categories": len(categorized_items),
        "total_records": sum(category_counts.values()),
        "category_counts": category_counts,
        "written_files_count": len(written_files)
    }
    with open(summary_file, "w", encoding="utf-8") as fp:
        json.dump(summary_data, fp, indent=2, ensure_ascii=False)

    return summary_data

