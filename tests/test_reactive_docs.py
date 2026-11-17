"""Executable checks for the synchronous reactivity guide (hmr 0.7.6.3)."""

import asyncio
import unittest
import warnings

from reactivity import async_derived, async_effect, batch, derived, effect, memoized, new_context, reactive, signal, state


class ReactiveDocsTests(unittest.TestCase):
    def test_signal_equality_and_untracked_read(self):
        s = signal(0)
        other = signal(10)
        seen = []
        with effect(lambda: seen.append((s.get(), other.get(track=False)))):
            self.assertFalse(s.set(0))
            other.set(11)
            self.assertEqual(seen, [(0, 10)])
            self.assertTrue(s.update(lambda x: x + 1))
            self.assertEqual(seen, [(0, 10), (1, 11)])

    def test_state_is_per_instance(self):
        class Counter:
            value = state(0)

        a, b = Counter(), Counter()
        seen = []
        with effect(lambda: seen.append(a.value)):
            b.value = 2
            a.value = 1
        self.assertEqual(seen, [0, 1])
        self.assertEqual(b.value, 2)

    def test_derived_lazy_cache_and_eager_pull(self):
        s = signal(0)
        calls, seen = [], []

        @derived
        def half():
            calls.append(s.get())
            return s.get() // 2

        self.assertEqual(calls, [])
        self.assertEqual(half(), 0)
        self.assertEqual(half(), 0)
        s.set(1)
        self.assertEqual(calls, [0])
        self.assertEqual(half(), 0)
        with effect(lambda: seen.append(half())):
            s.set(2)
            s.set(3)
        self.assertEqual(calls, [0, 1, 2, 3])
        self.assertEqual(seen, [0, 1])
        half.dispose()

    def test_memoized_invalidates_even_for_equal_result(self):
        s = signal(0)
        calls, seen = [], []

        @memoized
        def half():
            calls.append(s.get())
            return s.get() // 2

        self.assertEqual(calls, [])
        self.assertEqual(half(), 0)
        s.set(1)
        self.assertEqual(calls, [0])
        with effect(lambda: seen.append(half())):
            s.set(2)
            s.set(3)
        self.assertEqual(seen, [0, 1, 1])
        half.dispose()

    def test_deferred_effect_and_disposal(self):
        s = signal(0)
        seen = []
        e = effect(lambda: seen.append(s.get()), call_immediately=False)
        s.set(1)
        self.assertEqual(seen, [])
        e()
        s.set(2)
        e.dispose()
        s.set(3)
        self.assertEqual(seen, [1, 2])

    def test_dynamic_dependencies(self):
        choose_a, a, b = signal(True), signal(1), signal(2)  # noqa: FBT003
        seen = []
        with effect(lambda: seen.append(a.get() if choose_a.get() else b.get())):
            b.set(3)
            choose_a.set(False)
            a.set(4)
            b.set(5)
        self.assertEqual(seen, [1, 3, 5])

    def test_batch(self):
        a, b = signal(0), signal(0)
        seen = []
        with effect(lambda: seen.append((a.get(), b.get()))), batch():
            a.set(1)
            b.set(2)
            self.assertEqual(a.get(), 1)
            self.assertEqual(seen, [(0, 0)])
        self.assertEqual(seen, [(0, 0), (1, 2)])

    def test_context_isolation(self):
        ctx = new_context()
        s, other = ctx.signal(0), signal(0)
        seen = []
        with effect(lambda: seen.append((s.get(), other.get())), context=ctx):
            other.set(1)
            s.set(2)
        self.assertEqual(seen, [(0, 0), (2, 1)])

    def test_mapping_tracking_and_shallow_proxy(self):
        original = {"a": 1, "b": 2, "nested": {"value": 0}}
        values = reactive(original)
        seen = []
        with effect(lambda: seen.append(values["a"])):
            values["b"] = 3
            values["a"] = 2
            original["a"] = 4  # bypasses notification
        self.assertEqual(seen, [1, 2])
        self.assertEqual(values["a"], 4)
        self.assertIs(values["nested"], original["nested"])

    def test_list_and_set_tracking(self):
        items, members = reactive([1, 2]), reactive({1, 2})
        seen, contained = [], []
        with effect(lambda: seen.append(items[0])), effect(lambda: contained.append(1 in members)):
            items[1] = 3
            items[0] = 4
            members.add(3)
            members.remove(1)
        self.assertEqual(seen, [1, 4])
        self.assertEqual(contained, [True, False])
        with self.assertRaises(NotImplementedError):
            items[::2]


class ReactiveBoundaryTests(unittest.TestCase):
    def test_nested_batch_flushes_and_derived_read_is_stale(self):
        s = signal(0)
        total = derived(lambda: s.get() * 2)
        seen = []
        with effect(lambda: seen.append(total())), batch():
            s.set(1)
            self.assertEqual(total(), 0)
            with batch():
                s.set(2)
            self.assertEqual(seen, [0, 4])
            s.set(3)
        self.assertEqual(seen, [0, 4, 6])
        total.dispose()

    def test_custom_context_collection_structure_limitation(self):
        ctx = new_context()
        values = reactive({"a": 1}, context=ctx)
        seen = []
        # Read a key too so the effect has a valid custom-context dependency.
        with effect(lambda: seen.append((values["a"], len(values))), context=ctx):
            values["b"] = 2
            self.assertEqual(seen, [(1, 1)])
            values["a"] = 3
        self.assertEqual(seen, [(1, 1), (3, 2)])


class AsyncReactiveDocsTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracking_across_await_and_equal_result(self):
        s = signal(0)
        seen = []
        updated = asyncio.Event()
        recomputed = asyncio.Event()

        @async_derived
        async def half():
            await asyncio.sleep(0)
            result = s.get() // 2
            recomputed.set()
            return result

        @async_effect(call_immediately=False)
        async def printer():
            seen.append(await half())
            updated.set()

        await printer()
        updated.clear()
        s.set(2)
        await asyncio.wait_for(updated.wait(), 1)
        self.assertEqual(seen, [0, 1])
        recomputed.clear()
        s.set(3)
        await asyncio.wait_for(recomputed.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(seen, [0, 1])
        printer.dispose()
        half.dispose()

    async def test_concurrent_reads_share_recomputation(self):
        s = signal(2)
        calls = []
        entered, release = asyncio.Event(), asyncio.Event()

        @async_derived
        async def doubled():
            calls.append(s.get())
            entered.set()
            await release.wait()
            return s.get() * 2

        first, second = doubled(), doubled()
        await asyncio.wait_for(entered.wait(), 1)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), [4, 4])
        self.assertEqual(calls, [2])
        self.assertEqual(await doubled(), 4)
        doubled.dispose()

    async def test_custom_task_group_and_disposal(self):
        s = signal(1)
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async with asyncio.TaskGroup() as group:

            @async_effect(task_factory=lambda fn: group.create_task(fn()), call_immediately=False)
            async def printer():
                value = s.get()
                entered.set()
                await release.wait()
                seen.append(value)

            task = printer()
            await asyncio.wait_for(entered.wait(), 1)
            printer.dispose()
            s.set(2)
            release.set()
            with self.assertWarnsRegex(RuntimeWarning, "has no dependencies"):
                await task
        self.assertEqual(seen, [1])


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        unittest.main()
