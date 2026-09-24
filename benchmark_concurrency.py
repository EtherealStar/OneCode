import asyncio
import time

# 模拟参数
TOOL_CALLS = 10  # 假设模型一次性输出了 10 个工具调用
PREFLIGHT_TIME = (
    0.05  # 每次调用的前置校验时间（串行）：schema验证、权限检查等，假设 50ms
)
HANDLER_TIME = 1.0  # 工具实际执行的 I/O 耗时：例如读取文件、网络请求等，假设 1s


async def simulate_serial():
    start = time.time()
    for i in range(TOOL_CALLS):
        # 前置校验（始终串行）
        time.sleep(PREFLIGHT_TIME)  # noqa: ASYNC251 - 有意模拟阻塞式前置校验
        # 工具执行（串行等待）
        await asyncio.sleep(HANDLER_TIME)
    end = time.time()
    return end - start


async def simulate_concurrent():
    start = time.time()
    # 1. 前置校验：文档提到“并发批仍先串行 preflight 整批”
    for i in range(TOOL_CALLS):
        time.sleep(PREFLIGHT_TIME)  # noqa: ASYNC251 - 有意模拟阻塞式前置校验

    # 2. 工具并发执行：文档提到使用 asyncio.gather 并发执行
    tasks = [asyncio.sleep(HANDLER_TIME) for _ in range(TOOL_CALLS)]
    await asyncio.gather(*tasks)

    end = time.time()
    return end - start


async def main():
    print("【测试条件】")
    print(f"并发调用数量: {TOOL_CALLS}")
    print(f"单次校验耗时 (Preflight): {PREFLIGHT_TIME} 秒")
    print(f"单次执行耗时 (Handler): {HANDLER_TIME} 秒")
    print("-" * 40)

    serial_time = await simulate_serial()
    print(f"【串行模式】总耗时: {serial_time:.3f} 秒")

    concurrent_time = await simulate_concurrent()
    print(f"【并发模式】总耗时: {concurrent_time:.3f} 秒")

    print("-" * 40)
    speedup = serial_time / concurrent_time
    print(f"提升倍数: {speedup:.2f} 倍")
    print(f"节约时间: {serial_time - concurrent_time:.3f} 秒")


if __name__ == "__main__":
    asyncio.run(main())
