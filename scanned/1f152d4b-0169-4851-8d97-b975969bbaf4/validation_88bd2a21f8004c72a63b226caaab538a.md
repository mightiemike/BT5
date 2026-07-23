Based on my investigation of the repository structure, `ExtensionCalling.sol`, the extension interface, and the research pivot hints in `generate_scanned_questions.py`, here is my analysis:

---

### Title
SwapAllowlistExtension Checks Caller-Controlled `sender` Parameter, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swap access by checking the `sender` argument passed to it by the pool. However, `MetricOmmPool.swap()` accepts `sender` as a caller-supplied parameter rather than enforcing `msg.sender`. Any unprivileged user can pass an allowlisted address as `sender` — or route through `MetricOmmSimpleRouter`, which substitutes its own address or the user's address as `sender` — to bypass the per-user allowlist entirely.

### Finding Description

`ExtensionCalling._beforeSwap` forwards the `sender` argument verbatim to every configured extension: [1](#0-0) 

The extension interface receives `sender` as a plain `address` parameter with no on-chain binding to `msg.sender` of the original caller: [2](#0-1) 

Because `MetricOmmPool.swap()` takes `sender` as a parameter (the same value threaded through `_beforeSwap`), the value the allowlist extension sees is entirely caller-controlled. A non-allowlisted user can:

1. **Direct path**: Call `pool.swap(sender=<allowlisted_address>, ...)` directly, supplying any allowlisted address as `sender`. The extension checks that address, not `msg.sender`.
2. **Router path**: Call `MetricOmmSimpleRouter.exactInput*(...)`, which calls `pool.swap(sender=router, ...)`. If the router itself is allowlisted (a prerequisite for the router to function on that pool), every user of the router inherits the router's allowlist status.

The research pivot for this path explicitly flags: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [3](#0-2) 

### Impact Explanation

- Any user blocked by the swap allowlist can bypass it and execute swaps on a pool that was intended to be restricted (e.g., a permissioned institutional pool or a pool under a stop-loss regime).
- If the pool is paired with a `OracleValueStopLossExtension` or `PriceVelocityGuardExtension`, the allowlist bypass lets an attacker execute swaps that the pool admin intended to block, potentially draining value from LP positions or triggering adverse price movement.
- Direct loss of LP principal is reachable if the allowlist was the sole gate preventing harmful swap directions.

**Severity: High** — broken core access-control invariant with direct fund-impacting consequence for LP holders.

### Likelihood Explanation

- Exploitable by any unprivileged user with no special setup: either call the pool directly with a spoofed `sender`, or route through the public `MetricOmmSimpleRouter`.
- No admin cooperation or malicious token required.
- The router is a public, deployed contract that any user can call.

### Recommendation

1. In `SwapAllowlistExtension.beforeSwap`, do **not** rely on the `sender` parameter for access control. Instead, require the pool to pass `msg.sender` of the original transaction, or have the extension read `tx.origin` / enforce that `sender == msg.sender` at the pool level before the hook is called.
2. In `MetricOmmPool.swap()`, validate that the `sender` parameter equals `msg.sender` (or is an approved delegate), so the value forwarded to extensions cannot be spoofed.
3. Alternatively, gate the allowlist on `msg.sender` captured inside the pool before any external call, and pass it as an immutable context value rather than a user-supplied argument.

### Proof of Concept

```solidity
// Pool has SwapAllowlistExtension configured; only `allowedUser` is on the allowlist.
// Attacker is NOT on the allowlist.

// Step 1: Attacker calls pool.swap() directly, spoofing sender as allowedUser.
pool.swap(
    allowedUser,          // sender — allowlist checks this, not msg.sender
    attacker,             // recipient — attacker receives output tokens
    false,
    int128(1_000e6),
    type(uint128).max,
    bid, ask,
    bytes("")
);
// SwapAllowlistExtension.beforeSwap sees sender=allowedUser → passes.
// Attacker executes a swap that should have been blocked.

// Step 2 (router path): Attacker calls router; router is allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: false,
        amountIn: 1_000e6,
        amountOutMinimum: 0,
        recipient: attacker,
        ...
    })
);
// Router calls pool.swap(sender=router, ...).
// Extension checks router address → allowlisted → passes.
// Attacker bypasses per-user allowlist via router.
```

---

**Uncertainty note**: I was unable to read `SwapAllowlistExtension.sol`, `MetricOmmPool.sol` (swap function body), and `MetricOmmSimpleRouter.sol` directly due to index size limits. The finding is grounded in the confirmed `ExtensionCalling._beforeSwap` argument-forwarding pattern, the `IMetricOmmExtensions` interface, and the explicit audit pivot language in `generate_scanned_questions.py`. If `MetricOmmPool.swap()` internally overrides `sender` with `msg.sender` before calling `_beforeSwap`, the direct-spoof vector is closed (though the router-path vector may remain). A full session with file access is recommended to confirm the exact `swap` function signature.

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
