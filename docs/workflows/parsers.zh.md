# 用经验证样本扩展解析

先读导入报告，明确平台/命令组合。缺解析器、刻意保留 raw 的命令和已有解析器故障是不同问题。保留原始采集不变，本示例使用独立合成根。

## 无模型运行两步接口

示例故意使用 `demo_os` 及虚构双列表格，只演示准备、校验和冻结，不代表支持真实厂商命令。选择**新的**绝对目录和已安装解释器：

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-parser-demo
mkdir -p "$OLAV_HOME"
"$SKILLS_PYTHON" -I -B - <<'PYTHON'
import json, os, subprocess, sys
from pathlib import Path
home = Path(os.environ["OLAV_HOME"])
samples = [
    {"device": "demo1", "raw_output": "alpha up\n"},
    {"device": "demo2", "raw_output": "beta down\n"},
]
payload = {"platform": "demo_os", "command": "show demo status", "samples": samples}
def call(script, value):
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "olav_skills", "run", "learner", script],
        input=json.dumps(value), text=True, capture_output=True, check=True, cwd=home)
    (home / (script + ".stderr.log")).write_text(proc.stderr)
    result = json.loads(proc.stdout)
    (home / (script + ".json")).write_text(json.dumps(result, indent=2))
    assert result["status"] == "ok", result
    return result
call("prepare_learn", payload)
parser = "# OLAV_DSL: textfsm\nValue NAME (\\S+)\nValue STATE (\\S+)\n\nStart\n  ^${NAME}\\s+${STATE}$$ -> Record\n"
result = call("finish_learn", {**payload, "parser_response": parser})
assert result["parsed_rows"] == [1, 1]
assert result["quarantined"] is False
print(json.dumps(result, indent=2))
PYTHON
```

审阅保存的草拟 prompt 与返回模板路径。正常使用时，两次调用之间由人或宿主模型写候选；脚本自身不调用模型端点。即使退出码零，也要检查失败状态。

## 审阅启用及泛化

TextFSM 成功即启用，单样本也如此。Python 需额外 AST/沙箱验证，少于两个样本会隔离。查看 `quarantined`，不要把单样本成功当成广泛验收。

真实使用前，对不同设备值、可选列、多行变体、无邻居及错误输出等独立样本，核对预期字段和行。不要把单台设备地址/ASN 写死。纯解析器输入文本、返回记录，不应读文件、执行命令或联网。

冻结解析器影响未来解析/导入，本示例不会重解析旧行、修复生产数据，也不证明所有固件兼容。保留候选源码、样本来源、预期结果和失败诊断供审阅。字段见 [learner](../skills/learner.md)，写入边界见[安全](../safety.md)。
