"""从 飞书工具 schema sheet 导出的 annotated_csv JSON 提取 schema.json。

用法:
  python -m domains.extract_schema <schema_sheet.json> <domain> <domain_key>

schema sheet 列: 0=· 1=建议人 2=name_en 3=title 4=url 5=description 6=note 7=parameters 8=output 9=example
仅打印合法 schema JSON 到 stdout。
"""
import json, csv, io, re, sys


def parse_sheet(path):
    d = json.load(open(path))
    pure = '\n'.join(re.sub(r'^\[row=\d+\]\s?', '', ln)
                     for ln in d['data']['annotated_csv'].rstrip('\n').split('\n'))
    return [r for r in csv.reader(io.StringIO(pure)) if r and any(c.strip() for c in r)]


def main():
    path, domain, domain_key = sys.argv[1], sys.argv[2], sys.argv[3]
    tools = []
    for r in parse_sheet(path):
        name = (r[2].split('\n')[0]).strip() if len(r) > 2 else ''
        if not re.fullmatch(r'[a-z][a-z0-9_]+', name or ''):
            continue
        params = r[7] if len(r) > 7 else ''
        try:
            # 飞书 CSV 的 JSON 列含：字符串内字面换行/制表、以及 `""` 双重引号。
            # 用状态机：仅在 JSON 字符串内部把裸 \n/\t 转成 \\n/\\t，控制字符清空格。
            _fix = []
            _in = False
            _esc = False
            for _ch in params:
                if _esc:
                    _fix.append(_ch); _esc = False
                elif _ch == "\\":
                    _fix.append(_ch); _esc = True
                elif _ch == '"':
                    _fix.append(_ch); _in = not _in
                elif _in and _ch == "\n":
                    _fix.append("\\n")
                elif _in and _ch == "\t":
                    _fix.append("\\t")
                elif ord(_ch) < 32 and _ch not in "\n\t\r":
                    _fix.append(" ")
                else:
                    _fix.append(_ch)
            ps = json.loads("".join(_fix))
        except Exception:
            continue
        paramschema = ps.get('parameters', ps) if isinstance(ps, dict) else {}
        desc = (r[5] or '').strip() if len(r) > 5 else ''
        tools.append({'tool_name': name, 'description': desc or name,
                      'parameters': paramschema})
    out = {'domain': domain, 'domain_key': domain_key, 'tools': tools}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()