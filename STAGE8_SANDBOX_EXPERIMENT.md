# 阶段八：沙箱后代进程清理与边界实验

能力主题：学生端学习路线、代码提交与结果反馈；本轮角度：受控故障注入与性能边界。

## 问题与影响

本轮排查围绕学生提交后的 C++ 沙箱执行链路，重点检查超时、输出超限、正常退出和隔离建立失败时是否会留下子进程。直接子进程退出不等于它启动的后代也退出；如果后代继续运行，下一次提交可能受到资源占用、文件残留或输出干扰的影响。

影响对象是运行学生代码的沙箱 worker。期望行为是：每个执行请求只允许自己的进程树运行，所有结束路径都清理后代；隔离建立失败时不恢复用户进程，并返回明确失败。

## 先观察再假设

源码观察：`utils/sandbox_runner.py::_run_bounded_process` 负责启动、限时读取 stdout/stderr、判断超时或输出超限并回收进程；原有直接 `process.kill()` 只覆盖父进程，不能证明后代已经退出。

两个根因假设：

1. 结束路径只终止直接子进程，后代可能继续运行并在延迟后写入 marker。
2. Windows 下如果用户代码在隔离边界建立前就开始执行，后续再补 Job Object 不能覆盖启动窗口。

## 受控实验

实验均在 Windows 隔离 worktree 中执行，单次并发为 1 个请求，输入规模为 1 个父进程加 1 个后代进程。后代先写入 PID 文件，再等待 0.4 秒，若未被清理则写入 `descendant-survived` marker；父进程分别设置 0.1 秒超时、正常退出或 stdout 256 字节并将测试上限压到 64 字节。Windows 专属用例还故意让 Job Object 创建失败，检查用户代码没有执行。

三次独立运行 `tests/test_sandbox_process_cleanup.py`：

| 次数 | 结果 | pytest 用时 | PowerShell 墙钟时间 |
| --- | --- | ---: | ---: |
| 1 | 7 passed，退出码 0 | 4.79 s | 5392 ms |
| 2 | 7 passed，退出码 0 | 4.73 s | 5407 ms |
| 3 | 7 passed，退出码 0 | 4.77 s | 5329 ms |

合并后的相关回归命令：

```powershell
& D:\CodexDownloads\codesense-stage14-venv\Scripts\python.exe -m pytest tests/test_sandbox_process_cleanup.py tests/test_sandbox_output_limits.py -q --disable-warnings
```

实际结果：`15 passed in 5.64s`，退出码 0。日志证明 timeout、normal exit、stdout limit、空 PID 文件、启动前 Job Object 失败和后续正常运行均通过；marker 未生成，失败关闭用例没有执行用户代码。

## 改动范围

- `utils/sandbox_runner.py`
  - Windows 使用挂起创建，先建立 kill-on-close Job Object 并成功归组后再恢复进程。
  - POSIX 使用独立 process group；Windows/POSIX 结束路径都递归终止后代，并保留直接子进程兜底。
  - 正常退出、超时、stdout/stderr 超限、启动失败和恢复失败都关闭句柄与管道。
- `tests/test_sandbox_process_cleanup.py`
  - 覆盖超时、正常退出、输出超限、连续下一次运行、后代启动时序、Job Object 失败关闭和空 PID 文件。

没有修改数据库结构、权限、部署配置或核心接口。旧的 `reason` 值和正常返回结果保持兼容；新增的 `launch_error` 只用于隔离建立/恢复失败。

## 回滚与边界

回滚方式是回退本 PR 的提交，不需要数据库或部署回滚。当前实验只证明本机 Windows/POSIX 进程模型和测试桩中的清理行为；没有证明生产账户权限、容器/cgroup、Windows 服务账户、真实 g++ 编译矩阵、资源配额上限或多 worker 并发下的系统级隔离。

本次是 CLI/进程实验，没有浏览器界面，因此没有生成 UI 截图；可复核证据是上述三次完整 pytest 输出、退出码、耗时和 marker/进程清理断言。后续若接入真实生产隔离适配器，应单独验证权限、资源配额、进程迁移和多并发场景。
