"""从 测试集 sheet 导出的 annotated_csv JSON 生成 testset.json。

列: 0=业务域 1=query 2=意图 3=期望工具 4=期望参数
期望参数是内嵌 JSON 字符串（多行，"" 转义）。导出为标准 testset.json records 列表。
"""
import json, csv, io, re, sys


def main():
    path, domain, domain_key = sys.argv[1], sys.argv[2], sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 else ('testset.json' if False else None)
    d = json.load(open(path))
    pure = '\n'.join(re.sub(r'^\[row=\d+\]\s?', '', ln)
                     for ln in d['data']['annotated_csv'].rstrip('\n').split('\n'))
    rows = [r for r in csv.reader(io.StringIO(pure)) if r and r[0]]
    records = []
    unparse = 0
    for r in rows[1:]:
        if len(r) < 5 or not r[1].strip():
            continue
        q = r[1].strip()
        tool = r[3].strip() if len(r) > 3 else ''
        pstr = r[4].strip() if len(r) > 4 else ''
        try:
            params = json.loads(pstr) if pstr else {}
        except Exception:
            unparse += 1
            params = {}
        records.append({'query': q, 'expected_tool': tool, 'expected_params': params})
    payload = {'domain': domain, 'domain_key': domain_key, 'count': len(records),
               'records': records}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    if unparse:
        sys.stderr.write(f'!! {unparse} unparseable params\n')


if __name__ == '__main__':
    main()