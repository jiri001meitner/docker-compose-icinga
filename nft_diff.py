#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nft_diff.py — vyrobí deklarativní diff nftables: obsah COMPLETE minus GLOBAL.

Vstup:
    GLOBAL.nft  - "globální" pravidla (base)
    COMPLETE.nft- kompletní konfigurace (globální + app)

Výstup:
    Declarativní snippet (table { chain { ... } }) jen s app-specific částmi.
    Formátování a odsazení kopíruje COMPLETE.

Pravidla:
    - Nová tabulka/řetěz v COMPLETE → vypíše se celý (včetně headerů).
    - Řetěz existuje v obou → vypíší se jen nová pravidla (bez headerů).
    - Base chain headery se neduplikují, aby šel výstup bezpečně includovat.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# -------------------------- util funkce --------------------------


def strip_comments(text: str) -> str:
    """Remove /* ... */, // ... and # ... comments from the text."""
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out_lines: List[str] = []
    for line in no_block.splitlines():
        line = re.sub(r"\s*//.*$", "", line)
        line = re.sub(r"\s*#.*$", "", line)
        out_lines.append(line)
    return "\n".join(out_lines)


def normalize_spaces(s: str) -> str:
    """Collapse whitespace into single spaces and strip ends."""
    return re.sub(r"\s+", " ", s.strip())


def normalize_rule(s: str) -> str:
    """Normalize a rule line for equality compare (ignore trailing ';' and spacing)."""
    s = s.strip()
    if s.endswith(";"):
        s = s[:-1]
    return normalize_spaces(s)


# --------------------------- datové modely ---------------------------


@dataclass
class Chain:
    """Single nftables chain with preserved indentation and order."""

    name: str
    indent: str
    header_lines: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    blank_between_header_and_rules: bool = False

    _norm_rules: Set[str] = field(default_factory=set, init=False, repr=False)

    def finalize(self) -> None:
        """Compute normalized rule set once for comparisons."""
        self._norm_rules = {normalize_rule(ln.lstrip()) for ln in self.rules}

    def normalized_rules(self) -> Set[str]:
        """Expose normalized rule set for external comparison (read-only)."""
        return set(self._norm_rules)

    def diff_rules_originals(self, other: Optional["Chain"]) -> List[str]:
        """
        Return ORIGINAL rule lines that are present here but not in `other`.
        Comparison is done on normalized forms. Order is preserved from self.
        """
        if other is None:
            return list(self.rules)
        other_norm = other.normalized_rules()
        return [
            ln for ln in self.rules
            if normalize_rule(ln.lstrip()) not in other_norm
        ]


@dataclass
class Table:
    """nftables table consisting of ordered chains."""

    family: str
    name: str
    indent: str
    chains_in_order: List[str] = field(default_factory=list)
    chains: Dict[str, Chain] = field(default_factory=dict)

    def key(self) -> Tuple[str, str]:
        """Key usable in dicts/maps."""
        return (self.family, self.name)


@dataclass
class Ruleset:
    """Parsed nftables config: ordered tables with their chains."""

    tables_in_order: List[Tuple[str, str]] = field(default_factory=list)
    tables: Dict[Tuple[str, str], Table] = field(default_factory=dict)

    @staticmethod
    def parse(text: str) -> "Ruleset":
        """Parse nftables declarative syntax into a Ruleset."""
        ruleset = Ruleset()
        clean = strip_comments(text)
        idx, total = 0, len(clean)

        while True:
            match = re.search(
                r"(^[ \t]*)table\s+(\S+)\s+(\S+)\s*\{", clean[idx:], flags=re.M
            )
            if not match:
                break

            indent, fam, tname = match.group(1), match.group(2), match.group(3)
            brace_start = idx + match.end() - 1
            brace_end = _find_matching_brace(clean, brace_start)
            body = clean[brace_start + 1 : brace_end]

            table = Table(family=fam, name=tname, indent=indent)
            _parse_table_body_into(body, table)
            for chain in table.chains.values():
                chain.finalize()

            ruleset.tables[table.key()] = table
            ruleset.tables_in_order.append(table.key())

            idx = brace_end + 1
            if idx >= total:
                break

        return ruleset

    def get_table(self, fam: str, name: str) -> Optional[Table]:
        """Return table by (family, name) or None."""
        return self.tables.get((fam, name))

    def render_declarative_delta(self, base: "Ruleset") -> str:
        """Render declarative diff: only new tables/chains/rules compared to base."""
        output: List[str] = []
        for key in self.tables_in_order:
            fam, tname = key
            current = self.tables[key]
            base_tbl = base.get_table(fam, tname)

            to_render = _compute_chains_to_render(current, base_tbl)
            if not to_render:
                continue

            output.append(f"{current.indent}table {fam} {tname} {{")
            first = True
            for cname, chain, extras in to_render:
                if not first:
                    output.append("")  # blank line between chains, mirrors COMPLETE
                first = False

                output.append(f"{chain.indent}chain {cname} {{")
                if extras is None:
                    # New chain: print entire content including headers.
                    output.extend(chain.header_lines)
                    if chain.blank_between_header_and_rules and chain.rules:
                        output.append("")
                    output.extend(chain.rules)
                else:
                    # Existing chain: only rules that are not in base.
                    output.extend(extras)
                output.append(f"{chain.indent}}}")
            output.append(f"{current.indent}}}")

        return "\n".join(output) + ("\n" if output else "")


# ------------------------- parsing helpery -------------------------


def _find_matching_brace(text: str, open_pos: int) -> int:
    """Given index of '{', return index of matching '}' or raise ValueError."""
    if text[open_pos] != "{":
        raise ValueError("brace scan requires position at '{'")
    depth = 0
    for pos in range(open_pos, len(text)):
        char = text[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return pos
    raise ValueError("unmatched '{' found")


def _parse_table_body_into(body: str, table: Table) -> None:
    """Parse table body and fill `table` with chains while preserving formatting."""
    idx = 0
    while True:
        match = re.search(r"(^[ \t]*)chain\s+(\S+)\s*\{", body[idx:], flags=re.M)
        if not match:
            break

        indent, cname = match.group(1), match.group(2)
        brace_start = idx + match.end() - 1
        brace_end = _find_matching_brace(body, brace_start)

        chain = Chain(name=cname, indent=indent)

        # Preserve order; split header lines from rules and track blank line.
        saw_header = False
        pending_blank = False
        for line in body[brace_start + 1 : brace_end].splitlines():
            line = line.rstrip()
            if not line.strip():
                if saw_header and not chain.rules:
                    pending_blank = True
                continue

            content = line.lstrip()
            is_header = content.endswith(";") and (
                content.startswith("type ")
                or " hook " in f" {content} "
                or " priority " in f" {content} "
                or content.startswith("policy ")
                or content.startswith("flags ")
            )

            if is_header and not chain.rules:
                chain.header_lines.append(line)
                saw_header = True
            else:
                if saw_header and pending_blank:
                    chain.blank_between_header_and_rules = True
                    pending_blank = False
                chain.rules.append(line)

        table.chains[cname] = chain
        table.chains_in_order.append(cname)

        idx = brace_end + 1
        if idx >= len(body):
            break


def _compute_chains_to_render(
    current: Table, base_tbl: Optional[Table]
) -> List[Tuple[str, Chain, Optional[List[str]]]]:
    """
    Decide which chains to render and whether to print full chain (None)
    or only extras (List[str]).
    """
    result: List[Tuple[str, Chain, Optional[List[str]]]] = []
    if base_tbl is None:
        # Entire table is new.
        for cname in current.chains_in_order:
            result.append((cname, current.chains[cname], None))
        return result

    # New chains.
    for cname in current.chains_in_order:
        if cname not in base_tbl.chains:
            result.append((cname, current.chains[cname], None))

    # Existing chains with extra rules.
    for cname in current.chains_in_order:
        if cname in base_tbl.chains:
            chain = current.chains[cname]
            extras = chain.diff_rules_originals(base_tbl.chains[cname])
            if extras:
                result.append((cname, chain, extras))

    return result


# ------------------------------ main ------------------------------


def main(argv: List[str]) -> int:
    """CLI entrypoint. Usage: ./nft_diff.py GLOBAL.nft COMPLETE.nft > APP_DELTA.nft"""
    if len(argv) != 3:
        sys.stderr.write(f"Použití: {argv[0]} GLOBAL.nft COMPLETE.nft\n")
        return 2

    try:
        with open(argv[1], "r", encoding="utf-8") as f:
            glob_txt = f.read()
        with open(argv[2], "r", encoding="utf-8") as f:
            comp_txt = f.read()
    except OSError as exc:
        sys.stderr.write(f"Chyba při čtení: {exc}\n")
        return 1

    try:
        base = Ruleset.parse(glob_txt)
        comp = Ruleset.parse(comp_txt)
    except ValueError as exc:
        sys.stderr.write(f"Chyba při parsování: {exc}\n")
        return 1

    delta = comp.render_declarative_delta(base)
    sys.stdout.write(delta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
