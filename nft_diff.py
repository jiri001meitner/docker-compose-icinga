#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nft_diff.py — deklarativní diff nftables: COMPLETE minus GLOBAL.

Nové:
  • Single-file „clean“ mód: 1 vstup → normalizovaný výstup
    (xt→masquerade/dnat/snat, counters→counter).
  • Robustní regexy pro `xt target ...` a volitelné mezery u `to:`.
  • Dopočet chybějícího DNAT cíle z `table ip6 filter` (Docker):
    z řádků `ip6 daddr <ADDR> ... (tcp|udp) dport <PORT> ... accept`
    odvodí cíl a přepíše `xt target "DNAT"` → `dnat to [ADDR]:PORT`.

Dvouargumentový mód:
  • Normalizace:
      - `xt target "MASQUERADE"`  → `masquerade`
      - `xt target "DNAT" to:DEST` → `dnat to DEST`
      - `xt target "SNAT" to:DEST` → `snat to DEST`
      - `counter packets N bytes N` → `counter`
  • Výstup = jen nové tabulky/řetězy/pravidla oproti GLOBAL.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# -------------------------- util funkce --------------------------

def strip_comments(text: str) -> str:
    """Remove /*...*/, //..., #... comments."""
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
    """Normalize a rule for equality compare (ignore ';' at end, spaces)."""
    s = s.strip()
    if s.endswith(";"):
        s = s[:-1]
    return normalize_spaces(s)

# -------------------------- cleaning & inference -------------------

def _fix_xt_dnat_without_to(text: str) -> str:
    """
    `xt target "DNAT"` bez `to:` → zkus odvodit cíl z `filter` accept řádků:
      ip6 daddr <ADDR> ... (tcp|udp) dport <PORT> ... accept
    Pak přepiš na `dnat to [ADDR]:PORT`. Jinak ponech beze změny.
    """
    mapping: Dict[Tuple[str, str], Set[str]] = {}
    for m in re.finditer(
        r'ip6\s+daddr\s+([^\s]+).*?\b(tcp|udp)\s+dport\s+(\d+).*?\baccept\b',
        text,
    ):
        addr, proto, port = m.group(1), m.group(2), m.group(3)
        mapping.setdefault((proto, port), set()).add(addr)

    out_lines: List[str] = []
    for line in text.splitlines():
        bad = 'xt target "DNAT"' in line and 'to:' not in line
        if bad and 'dnat to' not in line:
            m = re.search(r'\b(tcp|udp)\s+dport\s+(\d+)\b', line)
            if m:
                proto, port = m.group(1), m.group(2)
                addrs = mapping.get((proto, port), set())
                if len(addrs) == 1:
                    addr = next(iter(addrs))
                    if ':' in addr and not (addr.startswith('[') and addr.endswith(']')):
                        addr_fmt = f'[{addr}]'
                    else:
                        addr_fmt = addr
                    line = line.replace(
                        'xt target "DNAT"', f'dnat to {addr_fmt}:{port}'
                    )
        out_lines.append(line)
    return "\n".join(out_lines)

def input_filter(text: str) -> str:
    """
    Normalizace vstupu (nahrazuje dřívější `nft_cleaning`):
      - `xt target "MASQUERADE"` → `masquerade`
      - `xt/SNAT|DNAT to:DEST`   → `snat|dnat to DEST`
      - `counter packets N bytes N` → `counter`
      - doplnění DNAT `to` z filter/DOCKER accept pravidel
    """
    xt_nat_re = re.compile(
        r'\bxt\s+target\s+"?(DNAT|SNAT|MASQUERADE)"?'
        r'(?:\s+to\s*:\s*([^\s;]+))?(?=\s|;|$)'
    )

    def _xt_nat_sub(m: re.Match) -> str:
        tgt = m.group(1)
        to = m.group(2)
        if tgt == "MASQUERADE":
            return "masquerade"
        if tgt == "DNAT":
            return f"dnat to {to}" if to else m.group(0)
        if tgt == "SNAT":
            return f"snat to {to}" if to else m.group(0)
        return m.group(0)

    text = xt_nat_re.sub(_xt_nat_sub, text)
    text = re.sub(r"\bcounter\s+packets\s+\d+\s+bytes\s+\d+\b", "counter", text)
    text = _fix_xt_dnat_without_to(text)
    return text

# --------------------------- datové modely -------------------------

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
        """Expose normalized rule set for external comparison."""
        return set(self._norm_rules)

    def diff_rules_originals(self, other: Optional["Chain"]) -> List[str]:
        """
        Return ORIGINAL rule lines present here but not in `other`.
        Comparison uses normalized forms. Order preserved from self.
        """
        if other is None:
            return list(self.rules)
        other_norm = other.normalized_rules()
        return [ln for ln in self.rules
                if normalize_rule(ln.lstrip()) not in other_norm]

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
                r"(^[ \t]*)table\s+(\S+)\s+(\S+)\s*\{",
                clean[idx:],
                flags=re.M,
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
        """Render declarative diff: new tables/chains/rules compared to base."""
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
                    output.append("")
                first = False
                output.append(f"{chain.indent}chain {cname} {{")
                if extras is None:
                    output.extend(chain.header_lines)
                    if chain.blank_between_header_and_rules and chain.rules:
                        output.append("")
                    output.extend(chain.rules)
                else:
                    output.extend(extras)
                output.append(f"{chain.indent}}}")
            output.append(f"{current.indent}}}")
        return "\n".join(output) + ("\n" if output else "")

# ------------------------- parsing helpery -------------------------

def _find_matching_brace(text: str, open_pos: int) -> int:
    """Given index of '{', return index of matching '}'."""
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
    """Parse table body and fill `table` with chains (preserve formatting)."""
    idx = 0
    while True:
        match = re.search(
            r"(^[ \t]*)chain\s+(\S+)\s*\{",
            body[idx:],
            flags=re.M,
        )
        if not match:
            break
        indent, cname = match.group(1), match.group(2)
        brace_start = idx + match.end() - 1
        brace_end = _find_matching_brace(body, brace_start)
        chain = Chain(name=cname, indent=indent)
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
    current: Table,
    base_tbl: Optional[Table],
) -> List[Tuple[str, Chain, Optional[List[str]]]]:
    """
    Decide which chains to render and whether to print full chain (None)
    or only extras (List[str]).
    """
    result: List[Tuple[str, Chain, Optional[List[str]]]] = []
    if base_tbl is None:
        for cname in current.chains_in_order:
            result.append((cname, current.chains[cname], None))
        return result
    for cname in current.chains_in_order:
        if cname not in base_tbl.chains:
            result.append((cname, current.chains[cname], None))
    for cname in current.chains_in_order:
        if cname in base_tbl.chains:
            chain = current.chains[cname]
            extras = chain.diff_rules_originals(base_tbl.chains[cname])
            if extras:
                result.append((cname, chain, extras))
    return result

# ------------------------------ main ------------------------------

def _read_file_or_stdin(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def main(argv: List[str]) -> int:
    """
    CLI:

      ./nft_diff.py GLOBAL.nft COMPLETE.nft  → diff (po normalizaci)
      ./nft_diff.py FILE.nft                 → pouze normalizace (clean mód)
      ./nft_diff.py -                        → normalizace STDIN → STDOUT
    """
    if len(argv) == 2:
        try:
            txt = _read_file_or_stdin(argv[1])
        except OSError as exc:
            sys.stderr.write(f"Chyba při čtení: {exc}\n")
            return 1
        sys.stdout.write(input_filter(txt))
        return 0

    if len(argv) != 3:
        sys.stderr.write(
            f"Použití: {argv[0]} GLOBAL.nft COMPLETE.nft\n"
            f"       nebo: {argv[0]} FILE.nft\n"
        )
        return 2

    try:
        glob_txt = _read_file_or_stdin(argv[1])
        comp_txt = _read_file_or_stdin(argv[2])
    except OSError as exc:
        sys.stderr.write(f"Chyba při čtení: {exc}\n")
        return 1

    try:
        glob_txt = input_filter(glob_txt)
        comp_txt = input_filter(comp_txt)
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
