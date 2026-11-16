"""企查查 Excel 清洗并入库。

流程（对应 V1.1 文档 §6 导入流程）：
    1. 读取 Excel → 校验表头 → 构造批次号
    2. 逐行解析企业字段 + 拆分电话号码（有效手机号/更多电话）
    3. 规范化号码（去空格、去 +86、识别手机号/固话/400 号）
    4. 归并：企业按 credit_code upsert；号码按 phone upsert，冲突归 conflict_count
    5. 写入 import_batch 并更新统计；向终端打印完整结果

用法：
    .venv\\Scripts\\python.exe sql\\012_import_qichacha.py "AI messages\\企业工商信息测试表.xlsx"
    .venv\\Scripts\\python.exe sql\\012_import_qichacha.py "path.xlsx" --operator 壕z
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.core.config import get_env  # noqa: E402


# ===== 列映射（Excel 中文表头 → customer_profile 字段）=====
COL_MAP_COMPANY: dict[str, str] = {
    "企业名称": "company_name",
    "登记状态": "registration_status",
    "法定代表人": "legal_person",
    "注册资本": "registered_capital",
    "成立日期": "established_at",
    "核准日期": "approved_at",
    "所属省份": "province",
    "所属城市": "city",
    "所属区县": "county",
    "统一社会信用代码": "credit_code",
    "纳税人识别号": "tax_no",
    "注册号": "registration_no",
    "组织机构代码": "org_code",
    "参保人数": "insured_count",
    "企业类型": "econ_kind",
    "所属行业": "industry",
    "曾用名": "original_name",
    "英文名": "english_name",
    "网址": "website",
    "企业地址": "address",
    "经营范围": "business_scope",
}
COL_VALID_PHONE = "有效手机号"
COL_MORE_PHONE = "更多电话"
COL_EMAIL = "邮箱"

SENTINEL_EMPTY = {"-", "", "—", "/"}  # 企查查里常拿来表示空值的占位
PHONE_SPLIT_RE = re.compile(r"[;；\n/]+")
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
LANDLINE_RE = re.compile(r"^\d{3,4}-?\d{7,8}$")


# ===== 工具函数 =====
def build_config() -> dict:
    password = get_env("MYSQL_PASSWORD")
    if not password:
        raise SystemExit("缺少 MYSQL_PASSWORD，请在 .env 中配置。")
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip() or "root",
        "password": password,
        "database": get_env("MYSQL_DATABASE", "agent_calling").strip() or "agent_calling",
        "charset": "utf8mb4",
        "connect_timeout": 8,
    }


def _clean_text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in SENTINEL_EMPTY:
        return None
    return s


def _clean_date(v):
    """Excel 日期列可能给 datetime / 字符串 / date；返回 'YYYY-MM-DD' 字符串或 None。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    if s in SENTINEL_EMPTY:
        return None
    # 企查查一般是 YYYY-MM-DD，也可能是 YYYY/MM/DD
    s = s.replace("/", "-")
    return s[:10]


def _clean_int(v) -> int | None:
    s = _clean_text(v)
    if s is None:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    n = int(m.group())
    return n if n >= 0 else None


def _normalize_phone(raw: str) -> str | None:
    s = _clean_text(raw)
    if s is None:
        return None
    # 去掉所有空格和全角空格
    s = re.sub(r"[\s\u3000]+", "", s)
    # 去掉 +86 前缀
    if s.startswith("+86"):
        s = s[3:]
    elif s.startswith("86") and len(s) >= 13:
        s = s[2:]
    # 全角转半角连字符
    s = s.replace("－", "-").replace("—", "-")
    return s or None


def _classify_phone(phone: str) -> str:
    if MOBILE_RE.match(phone):
        return "MOBILE"
    return "LANDLINE"  # 400、固话、其他都归入 LANDLINE


def split_phone_cells(row: dict[str, str | None]) -> list[tuple[str, str]]:
    """从一行里拆出所有号码 → [(规范化号码, VALID_CELL_PHONES/MORE_PHONE)]。"""
    out: list[tuple[str, str]] = []
    for raw, src in (
        (row.get(COL_VALID_PHONE), "VALID_CELL_PHONES"),
        (row.get(COL_MORE_PHONE), "MORE_PHONE"),
    ):
        if not raw:
            continue
        for piece in PHONE_SPLIT_RE.split(str(raw)):
            phone = _normalize_phone(piece)
            if phone:
                out.append((phone, src))
    return out


# ===== SQL =====
UPSERT_PROFILE = """
INSERT INTO customer_profile (
    credit_code, company_name, registration_status, legal_person,
    registered_capital, established_at, approved_at,
    province, city, county, region, address, website,
    tax_no, registration_no, org_code, insured_count,
    econ_kind, industry, original_name, english_name, business_scope,
    source_channel, first_batch_no, raw_json
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    company_name        = IF(VALUES(company_name)        <> '', VALUES(company_name),        company_name),
    registration_status = IF(VALUES(registration_status) <> '', VALUES(registration_status), registration_status),
    legal_person        = IF(VALUES(legal_person)        <> '', VALUES(legal_person),        legal_person),
    registered_capital  = IF(VALUES(registered_capital)  <> '', VALUES(registered_capital),  registered_capital),
    established_at      = IF(VALUES(established_at)      IS NOT NULL, VALUES(established_at),      established_at),
    approved_at         = IF(VALUES(approved_at)         IS NOT NULL, VALUES(approved_at),         approved_at),
    province            = IF(VALUES(province)            <> '', VALUES(province),            province),
    city                = IF(VALUES(city)                <> '', VALUES(city),                city),
    county              = IF(VALUES(county)              <> '', VALUES(county),              county),
    region              = IF(VALUES(region)              <> '', VALUES(region),              region),
    address             = IF(VALUES(address)             <> '', VALUES(address),             address),
    website             = IF(VALUES(website)             <> '', VALUES(website),             website),
    tax_no              = IF(VALUES(tax_no)              <> '', VALUES(tax_no),              tax_no),
    registration_no     = IF(VALUES(registration_no)     <> '', VALUES(registration_no),     registration_no),
    org_code            = IF(VALUES(org_code)            <> '', VALUES(org_code),            org_code),
    insured_count       = IF(VALUES(insured_count)       IS NOT NULL, VALUES(insured_count),       insured_count),
    econ_kind           = IF(VALUES(econ_kind)           <> '', VALUES(econ_kind),           econ_kind),
    industry            = IF(VALUES(industry)            <> '', VALUES(industry),            industry),
    original_name       = IF(VALUES(original_name)       IS NOT NULL, VALUES(original_name),       original_name),
    english_name        = IF(VALUES(english_name)        <> '', VALUES(english_name),        english_name),
    business_scope      = IF(VALUES(business_scope)      IS NOT NULL, VALUES(business_scope),      business_scope),
    raw_json            = VALUES(raw_json),
    last_synced_at      = NOW()
"""

UPSERT_CONTACT = """
INSERT INTO customer_contact (
    credit_code, phone, phone_type, phone_source,
    contact_name, is_primary, source_channel, import_batch_no
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    phone_source    = IF(VALUES(phone_source) IS NOT NULL, VALUES(phone_source), phone_source),
    phone_type      = VALUES(phone_type),
    contact_name    = IF(VALUES(contact_name) IS NOT NULL, VALUES(contact_name), contact_name),
    import_batch_no = VALUES(import_batch_no)
"""

INSERT_BATCH = """
INSERT INTO import_batch (
    batch_no, source_channel, file_name,
    total_rows, company_count, contact_count,
    duplicated_count, invalid_count, conflict_count,
    operator, status
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    total_rows       = VALUES(total_rows),
    company_count    = VALUES(company_count),
    contact_count    = VALUES(contact_count),
    duplicated_count = VALUES(duplicated_count),
    invalid_count    = VALUES(invalid_count),
    conflict_count   = VALUES(conflict_count),
    status           = VALUES(status),
    operator         = VALUES(operator)
"""


def gen_batch_no() -> str:
    today = datetime.now().strftime("%Y%m%d")
    # 随机后缀，避免同一天同文件重跑时冲突
    suffix = uuid.uuid4().hex[:4].upper()
    return f"IMP{today}-{suffix}"


def build_profile_values(row: dict, batch_no: str, raw_json: str) -> tuple | None:
    credit_code = _clean_text(row.get("统一社会信用代码"))
    company_name = _clean_text(row.get("企业名称"))
    if not credit_code or not company_name:
        return None  # 缺主键，跳过

    province = _clean_text(row.get("所属省份"))
    city = _clean_text(row.get("所属城市"))
    region_parts = [p for p in (province, city) if p]
    region = "".join(region_parts) if region_parts else None

    return (
        credit_code,
        company_name,
        _clean_text(row.get("登记状态")),
        _clean_text(row.get("法定代表人")),
        _clean_text(row.get("注册资本")),
        _clean_date(row.get("成立日期")),
        _clean_date(row.get("核准日期")),
        province, city, _clean_text(row.get("所属区县")),
        region,
        _clean_text(row.get("企业地址")),
        _clean_text(row.get("网址")),
        _clean_text(row.get("纳税人识别号")),
        _clean_text(row.get("注册号")),
        _clean_text(row.get("组织机构代码")),
        _clean_int(row.get("参保人数")),
        _clean_text(row.get("企业类型")),
        _clean_text(row.get("所属行业")),
        _clean_text(row.get("曾用名")),
        _clean_text(row.get("英文名")),
        _clean_text(row.get("经营范围")),
        "QICHACHA",
        batch_no,
        raw_json,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="企查查 Excel 清洗入库")
    parser.add_argument("excel", help="Excel 文件路径")
    parser.add_argument("--operator", default="壕z", help="导入操作人")
    parser.add_argument("--dry-run", action="store_true", help="只解析打印，不写数据库")
    args = parser.parse_args()

    xlsx_path = Path(args.excel)
    if not xlsx_path.is_absolute():
        xlsx_path = (PROJECT_ROOT / xlsx_path).resolve()
    if not xlsx_path.exists():
        print(f"[错误] 文件不存在：{xlsx_path}")
        return 1

    batch_no = gen_batch_no()
    print(f"=== 企查查导入批次 {batch_no}，文件 {xlsx_path.name} ===")
    if args.dry_run:
        print("! DRY-RUN 模式：只解析打印，不写数据库\n")

    # ---- 1. 读 Excel ----
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    headers_raw = next(rows_iter)
    headers = [str(c).strip() if c is not None else "" for c in headers_raw]

    # 校验必需列
    missing = [h for h in ("统一社会信用代码", "企业名称", COL_VALID_PHONE, COL_MORE_PHONE)
               if h not in headers]
    if missing:
        print(f"[错误] Excel 缺少必需列：{missing}")
        return 1

    rows: list[dict] = []
    total_rows = 0
    for raw in rows_iter:
        row = {headers[i]: raw[i] if i < len(raw) else None for i in range(len(headers))}
        rows.append(row)
        total_rows += 1

    print(f"读取 {total_rows} 行数据")

    # ---- 2. 解析 ----
    company_values_batch: list[tuple] = []          # 企业 upsert 批量
    contact_values_batch: list[tuple] = []          # 号码 upsert 批量
    file_credit_codes: set[str] = set()             # 本文件出现过的信用代码（企业去重）
    company_dedup: dict[str, bool] = {}             # 信用代码 → 是否已收集
    file_phones: dict[str, str] = {}                # 本文件规范化号码 → 所属 credit_code
    phone_conflicts: list[tuple[str, str, str]] = []  # 号码冲突记录 (phone, 文件企业, 库内企业)
    invalid_rows: list[int] = []                    # 无效行号（缺关键字段）
    file_invalid_phones: list[int] = []             # 本文件内号码冲突

    # 连库查已有号码归属（一次查完避免 N+1）
    try:
        conn = pymysql.connect(**build_config())
    except pymysql.MySQLError as exc:
        print(f"[连接失败] {exc}")
        return 1

    try:
        cur = conn.cursor()
        cur.execute("SELECT credit_code, phone FROM customer_contact")
        # 库内号码 → 所属 credit_code
        db_phone_owner: dict[str, str] = {p: c for c, p in cur.fetchall()}
        cur.close()
    except pymysql.MySQLError as exc:
        print(f"[读取库内号码失败] {exc}")
        conn.close()
        return 1

    for idx, row in enumerate(rows, start=1):
        # 企业字段
        raw_json_obj = {
            k: (_clean_text(row.get(k)) or None)
            for k in ("邮箱",)
        }
        # 也把整个原始行（企查查原文）一并存进 raw_json，方便追溯
        raw_json_obj["_raw_row"] = {k: str(v) if v is not None else None for k, v in row.items()}

        values = build_profile_values(row, batch_no, json.dumps(raw_json_obj, ensure_ascii=False))
        if values is None:
            invalid_rows.append(idx)
            print(f"  [无效] 行 {idx}：缺信用代码或企业名称，跳过")
            continue

        credit_code = values[0]
        if credit_code in company_dedup:
            print(f"  [文件内重复企业] 行 {idx} 信用代码 {credit_code}，跳过")
            continue
        company_dedup[credit_code] = True
        company_values_batch.append(values)
        file_credit_codes.add(credit_code)

        # 号码
        phones = split_phone_cells(row)
        # 同一企业内号码去重
        seen_in_company: set[str] = set()
        for phone, src in phones:
            if phone in seen_in_company:
                continue
            seen_in_company.add(phone)

            # 冲突 1：本文件另一企业已声明此号码
            if phone in file_phones:
                owner = file_phones[phone]
                if owner != credit_code:
                    phone_conflicts.append((phone, credit_code, f"文件内 {owner}"))
                    continue
            file_phones.setdefault(phone, credit_code)

            # 冲突 2：库内已归属其他企业
            db_owner = db_phone_owner.get(phone)
            if db_owner and db_owner != credit_code:
                phone_conflicts.append((phone, credit_code, db_owner))
                continue

            contact_values_batch.append((
                credit_code,
                phone,
                _classify_phone(phone),
                src,
                None,           # contact_name：一期不自动填法人
                1 if src == "VALID_CELL_PHONES" else 0,  # 有效手机号里最多选一个做主号
                "QICHACHA",
                batch_no,
            ))

    # ---- 3. 统计 ----
    company_count = len(company_values_batch)
    duplicated_count = len(file_invalid_phones)  # 号码内冲突已跳过，但也计入
    conflict_count = len(phone_conflicts)

    # ---- 4. 打印结果 ----
    print(f"\n=== 解析结果 ===")
    print(f"  归并企业：{company_count} 家（文件总行数 {total_rows}）")
    print(f"  号码待入库：{len(contact_values_batch)} 条")
    print(f"  无效行：{len(invalid_rows)}（行号 {invalid_rows}）")
    print(f"  号码归属冲突：{conflict_count} 条")
    for phone, file_owner, db_owner in phone_conflicts:
        print(f"    - {phone}：本文件归属 {file_owner}，数据库归属 {db_owner}，跳过")

    if args.dry_run:
        conn.close()
        print("\n[DRY-RUN 结束] 未写入数据库")
        return 0

    # ---- 5. 入库 ----
    try:
        cur = conn.cursor()

        # import_batch 先插（RUNNING）
        cur.execute(INSERT_BATCH, (
            batch_no, "QICHACHA", xlsx_path.name,
            total_rows, company_count, 0,  # contact_count 先写 0，后面改
            0, len(invalid_rows), conflict_count,  # duplicated_count 一期不单独算
            args.operator, "RUNNING",
        ))
        conn.commit()
        print(f"\n=== 入库批次 {batch_no} ===")

        # customer_profile
        cur.executemany(UPSERT_PROFILE, company_values_batch)
        print(f"  ✓ customer_profile upsert {cur.rowcount} 行（ON DUPLICATE 已包含旧行更新）")

        # customer_contact
        cur.executemany(UPSERT_CONTACT, contact_values_batch)
        contact_saved = cur.rowcount
        print(f"  ✓ customer_contact upsert {contact_saved} 行（已排除 {conflict_count} 条归属冲突）")

        # 更新 contact_count 冗余字段
        cur.execute(
            "UPDATE customer_profile p "
            "SET contact_count = (SELECT COUNT(*) FROM customer_contact c WHERE c.credit_code = p.credit_code) "
            "WHERE p.credit_code IN (" + ",".join(["%s"] * len(file_credit_codes)) + ")",
            tuple(file_credit_codes),
        )
        print(f"  ✓ 更新 {cur.rowcount} 家企业的 contact_count")

        # import_batch 改成 SUCCESS 并回写 contact_count
        cur.execute(
            "UPDATE import_batch SET contact_count = %s, status = 'SUCCESS' WHERE batch_no = %s",
            (contact_saved, batch_no),
        )
        conn.commit()
        print(f"  ✓ import_batch 标记 SUCCESS，contact_count = {contact_saved}")
        cur.close()
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[入库失败] {exc}")
        # 尝试把批次改成 FAILED
        try:
            cur.execute(
                "UPDATE import_batch SET status = 'FAILED', error_file = %s WHERE batch_no = %s",
                (str(exc)[:255], batch_no),
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
        return 1

    conn.close()

    # ---- 6. 汇总 ----
    print(f"\n=== 完成批次 {batch_no} ===")
    print(f"  企业 {company_count} 家，号码 {contact_saved} 条")
    print(f"  无效行 {len(invalid_rows)}，号码归属冲突 {conflict_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
