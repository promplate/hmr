---
icon: lucide/circle-dashed
title: Reactive Collections
---

# `#!py from reactivity.collections import *`

## Tracking and mutation

`reactive()` dispatches by input type. Mutable mappings, sets, and sequences are proxied, so writes through the wrapper also change the original. The corresponding `ReactiveMapping`, `ReactiveSet`, and `ReactiveSequence` constructors create shallow copies instead. Other objects are handled by `reactive_object_proxy`.

- Mapping reads track keys. Changing an unrelated key does not rerun a computation that only reads one key. Iterating keys or reading length tracks structural changes.
- Set membership reads track the requested member.
- Sequence reads track indices; structural edits can affect several indices.
- Nested dictionaries, lists, and object values remain ordinary values unless separately wrapped.

Mutate through the wrapper. Direct writes to the original container bypass notification and can also leave the wrapper's structural bookkeeping out of date.

### Current limitations

In hmr 0.7.6.3, sequence slices with a step other than `1` raise `NotImplementedError`. Object proxies require an instance `__dict__`; objects using only `__slots__` are not supported. Collection iteration/length tracking uses the default context internally in this version, so custom-context collections should not be assumed to provide fully isolated structural tracking.

::: reactivity.collections
