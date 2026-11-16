# healthcheck

仅使用 Python 标准库实现的多 URL 巡检、状态机、告警去重与历史落库工具。支持回放探测夹具，也支持直接发起 HTTP 探测。

## 目录结构

- `healthcheck/probe.py`：探测记录、实时 HTTP 探测、夹具加载、SQLite 历史库
- `healthcheck/fingerprint.py`：响应正文 sha256 指纹
- `healthcheck/baseline.py`：前 N 次延迟的中位数基线与阈值
- `healthcheck/states.py`：正常、可疑、故障、恢复四态状态机
- `healthcheck/alerts.py`：故障/恢复告警与同 URL 去重
- `healthcheck/report.py`：报告聚合、统计分布和 stdout 摘要
- `healthcheck/cli.py`、`healthcheck/__main__.py`：命令行入口
- `demo/probes.json`：5 个 URL、每个 URL 6 次探测的确定性夹具
- `tests/test_healthcheck.py`：覆盖全部判定口径的 `unittest`

## 快速运行

```bash
python -m unittest test_healthcheck -v
python -m healthcheck.cli --probes demo/probes.json --out out/report.json
```

demo 命令会：

1. 读取 `demo/probes.json`；
2. 运行统一状态机并生成 `out/report.json`；
3. 将每次探测写入 `out/history.db`；
4. 在 stdout 打印一行摘要：URL 数、当前故障数、当前可疑数、告警总数。

实时探测示例：

```bash
python -m healthcheck.cli \
  --urls https://example.com/health https://example.org/health \
  --out out/live-report.json
```

## 探测记录

每次探测都记录以下字段，失败探测也必须有延迟：

- `url`：目标 URL
- `timestamp`：UTC ISO-8601 时间
- `status_code`：HTTP 状态码；网络错误或超时时为 `null`
- `latency_ms`：毫秒级延迟，成功和失败都记录
- `response_length`：响应正文长度
- `content_sha256`：响应正文 sha256；失败且没有响应正文时为空字符串
- `ok`：状态码在 `[200, 400)` 内视为成功，其余 HTTP 状态、网络错误和超时均为失败
- `error`：网络/超时错误信息；HTTP 500 等有响应的状态码保留状态码和正文

## 状态机迁移条件

状态取值为 `normal`、`suspect`、`fault`、`recovery`。

- **正常 `normal`**：初始状态。失败只增加连续失败计数，不立即故障；成功且没有可疑信号时保持正常。
- **可疑 `suspect`**：只由两类信号触发：成功探测的内容指纹偏离基线，或成功探测延迟严格大于阈值。进入后保持可疑，直到指纹回到基线且延迟回到阈值以内，才回到正常。
- **故障 `fault`**：连续失败计数达到 `failure_threshold`（默认 3）时进入。第 N 次失败才迁移，前 N-1 次失败仍保持原状态。任意一次成功探测都会把失败计数清零。
- **恢复 `recovery`**：故障状态下第一次成功探测立即进入恢复，不需要连续成功两次。恢复是粘性状态，后续正常成功不再产生迁移或通知；只有出现新的可疑信号才进入可疑，或新的连续失败达到阈值才再次进入故障。

失败探测不建立内容指纹基线，也不参与指纹比较；其延迟仍进入延迟记录和基线计算。

## 指纹规则

- 指纹算法固定为 sha256，输出 64 位小写十六进制摘要。
- 每个 URL 的指纹基线取第一次成功探测的响应正文。
- 后续成功探测与该基线比较；不同即判为指纹变化。
- 状态码正常（200）但正文被替换时，会进入可疑，不产生告警。
- HTTP 500 等失败响应不会设置指纹基线。例如前两次失败、第三次才成功时，基线取第三次成功正文。

## 延迟基线

延迟基线不是固定毫秒数，也不是平均数，而是该 URL 前 `baseline_size` 次探测延迟的中位数：

- `baseline_size` 可配，默认 `3`；
- 每次探测都记录延迟，失败探测也参与前 N 次中位数；
- 样本数达到 `baseline_size` 后才建立基线并开始延迟判定；
- 阈值 = 中位数基线 × `latency_multiplier`，倍数默认 `3.0`；
- 当前延迟必须严格大于阈值才判可疑，等于阈值不判可疑；
- 偶数个样本时取中间两个数的平均值。

夹具中 E 的前三次延迟为 `100, 100, 300`：中位数基线是 `100ms`，阈值是 `300ms`，后续 `400ms` 严格超过阈值，因此进入可疑。若错误地使用平均数，基线会变成约 `166.67ms`、阈值 `500ms`，`400ms` 就无法被判为可疑。

## 抖动抑制与告警语义

- `failure_threshold` 可配，默认 `3`；只有连续失败达到该阈值才进入故障。
- 中途出现任意一次成功探测，连续失败计数立即清零。
- 进入故障时只发送一条 `fault` 告警；持续故障期间的第 4 次、第 5 次失败不会重复告警。
- 故障后的第一次成功探测立即转入 `recovery`，并只发送一条 `recovery` 通知。
- 可疑状态只进入状态机和 `transitions`，不会进入 `alerts`。
- 恢复后如果再次经历完整故障周期，可以产生新的一组故障/恢复告警；同一故障周期内仍严格去重。

## 阈值配置

| CLI 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--failure-threshold` | `3` | 连续多少次失败进入故障 |
| `--baseline-size` | `3` | 用前多少次延迟计算中位数基线 |
| `--latency-multiplier` | `3.0` | 延迟阈值倍数 |
| `--timeout` | `10.0` | 实时 HTTP 探测的单次超时秒数 |
| `--db` | `<out 所在目录>/history.db` | SQLite 历史库路径 |

## 报告字段

`out/report.json` 包含：

- `generated_at`：报告生成时间
- `config`：本次使用的阈值配置
- `totals.url_count`：URL 总数
- `totals.fault_count` / `suspect_count` / `recovery_count` / `normal_count`：最终状态分布
- `totals.alert_count`：告警总数
- `alert_distribution`：按类型统计，例如 `{"fault": 1, "recovery": 1}`
- `history_db`：SQLite 历史库路径
- `urls[]`：每个 URL 的详情

每个 URL 的详情包含：

- `final_state`：最终状态
- `fail_count`：报告处理结束时的连续失败计数
- `fingerprint_baseline`：首次成功正文的 sha256
- `fingerprint_changed`：最后一次成功探测是否偏离指纹基线
- `latency_baseline_ms`、`latency_limit_ms`、`latency_records_ms`：延迟基线、阈值和全部延迟记录
- `transitions`：状态迁移列表，每条含 `timestamp`、`from_state`、`to_state`、`reason`、`probe_index`
- `alerts`：该 URL 的故障和恢复告警；可疑不产生告警

## 历史落库

所有探测写入 SQLite 表 `probe_history`，字段与探测记录一致。可按 URL 查询：

```python
from healthcheck.probe import HistoryStore

with HistoryStore("out/history.db") as store:
    rows = store.fetch_for_url("https://example.invalid/a")
```

## demo 夹具预期

`demo/probes.json` 中 A-E 各有 6 次探测：

- A：全部 200，最终正常，无告警
- B：连续 4 次 500 后 2 次 200，第 3 次失败进入故障，第 1 次成功进入恢复；仅 1 条故障、1 条恢复告警
- C：2 次 500 后 4 次 200，失败计数最高为 2，第三次成功后清零；不进入故障或可疑，指纹基线取第三次成功正文
- D：前 3 次与后 3 次正文不同但状态码均为 200，进入并保持可疑
- E：正文不变，延迟为 `100, 100, 300, 400, 400, 400`，中位数基线 100、阈值 300，进入并保持可疑

最终恰好 B、D、E 三个 URL 曾进入故障或可疑；告警总数固定为 2。
