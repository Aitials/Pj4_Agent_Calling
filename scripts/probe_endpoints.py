"""好客搜接口探测 v2 - 按浏览器实际抓到的路径，慢速逐个测"""
import httpx, json, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.clients.haokesou_auth import ensure_cookie

cookie = ensure_cookie()
print(f'Cookie OK')

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'http://whxt.jshks.com/user/plan/newindex',
    'Content-Type': 'application/x-www-form-urlencoded',
}

cookies = {}
for part in cookie.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1)
        cookies[k] = v

base = 'http://whxt.jshks.com'

# 按浏览器 network 实际抓到的接口 + 合理参数
endpoints = [
    # (名称, 路径, POST 表单参数)
    ('客户列表', '/user/Member/ajax_intentional_member', {'page':'1','limit':'10','phone':'','name':'','sitchair':'','config_id':'','startCreateDate':'','endCreateDate':'','min_call_count':'','max_call_count':''}),
    ('历史通话记录', '/user/callrecord/get_new_call_historical_record', {'page':'1','limit':'10','record_type':'1','select_type':'1','order':'desc','type':'call_time','status[]':[],'level[]':[],'call_times_sel':'<=','effective_times_sel':'<=','hit_times_sel':'<=','affirm_times_sel':'<=','neutral_times_sel':'<=','negative_times_sel':'<='}),
    ('当天通话记录', '/user/callrecord/get_new_call_record', {'page':'1','limit':'10','record_type':'1','select_type':'1','order':'desc','type':'call_time'}),
    ('任务统计', '/user/Plan/ajax_task_statistics', {'page':'1','limit':'10'}),
    ('通话详情/客户详情', '/user/member/backdetail', {'mobile':'13800001111','taskId':'1','recordId':'1','froms':'record','select_type':'1'}),
    ('通话监控', '/user/callrecord/monitor', {}),
    ('搜索条件', '/user/callrecord/get_search_condition', {}),
    ('任务列表(通话页)', '/user/callrecord/get_task_list', {}),
    ('开关标签', '/user/extension/getSwitchTag', {}),
    ('综合概况-总数据', '/user/index/get_every_data', {}),
    ('综合概况-任务列表', '/user/index/get_task_list_api', {}),
    ('综合概况-任务数据', '/user/index/get_task_data_api', {}),
    ('综合概况-用户信息', '/user/index/get_user_info', {}),
    ('可用机器人数', '/user/plan/get_usable_robot_count_api', {}),
    ('微信推送配置', '/user/Weixin/show_weixin_info', {}),
    ('短信模板数据', '/user/sms/gettemplatedata', {}),
    ('短信发送记录', '/user/sms/ajax_sendrecord', {'page':'1','limit':'10'}),
    ('消息中心', '/user/message/ajax_my', {'page':'1','limit':'10'}),
    ('客户详情', '/user/Member/get_constomerDetail', {'mobile':'13800001111'}),
]

results = []
with httpx.Client(timeout=10) as client:
    for name, path, params in endpoints:
        time.sleep(0.8)  # 慢速，避免触发频率限制
        try:
            r = client.post(base + path, headers=headers, cookies=cookies, data=params)
            body = r.text.strip()
            
            code = None
            msg = None
            total = None
            try:
                j = r.json()
                code = j.get('code') if isinstance(j, dict) else None
                msg = j.get('msg') if isinstance(j, dict) else None
                data = j.get('data') if isinstance(j, dict) else None
                if isinstance(data, dict):
                    total = data.get('total') or data.get('count')
            except:
                pass
            
            is_json = body.startswith('{')
            has_error_page = 'ErrorException' in body or '<!DOCTYPE html>' in body[:50]
            is_good = (r.status_code == 200 and is_json and not has_error_page)
            
            results.append({'name':name,'path':path,'status':r.status_code,'code':code,'msg':msg,'total':total,'good':is_json,'body':body[:300]})
            mark = '✅' if is_json else ('⚠️' if r.status_code==200 else '❌')
            print(f"  {mark} {path:<55} code={code} total={total} msg={str(msg)[:20] if msg else ''}")
        except Exception as e:
            results.append({'name':name,'path':path,'error':str(e)[:80]})
            print(f"  ❌ {path:<55} ERR: {str(e)[:50]}")

print(f"\n{'='*70}")
json_ok = [r for r in results if r.get('good')]
print(f"✅ 返回 JSON: {len(json_ok)}/{len(results)}")
for r in json_ok:
    print(f"   {r['name']:<12} {r['path']:<55} code={r['code']} total={r['total']}")

with open('state/_probe_v2.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
