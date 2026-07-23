### Title
`SwapAllowlistExtension` gates the router's address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives the pool's `msg.sender` as the `sender` argument. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router (the only way to let legitimate users trade through the periphery) simultaneously opens the gate to every unpermissioned address on the network.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its policy with:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument the pool forwards from its own `msg.sender` — i.e. whoever called `pool.swap(...)`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) executes a swap, it calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [2](#0-1) 

The pool's `swap` function receives `msg.sender = router`. It therefore passes the **router's address** as `sender` to every registered extension, including `SwapAllowlistExtension`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Consequence |
|---|---|
| Allowlist the router | Every address on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter` |
| Do not allowlist the router | Every allowlisted user is silently blocked from using the supported periphery path |

The protocol's own audit-target document acknowledges this exact concern:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*
> *"Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [3](#0-2) 

The current implementation does not satisfy that invariant.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g. KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) is fully bypassed the moment the admin allowlists the router. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity. This constitutes a direct policy bypass on a live pool, with potential for unauthorized extraction of LP value, front-running of curated order flow, or violation of regulatory/contractual access controls.

---

### Likelihood Explanation

The bypass requires only that the pool admin allowlists the router — a natural and expected action for any admin who wants legitimate users to access the pool through the supported periphery. The router is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges or setup.

---

### Recommendation

The pool's `swap` function should forward the **original initiating user** as `sender` to extensions, not its own `msg.sender`. One approach: add an explicit `sender` parameter to `IMetricOmmPoolActions.swap` that the router populates with `msg.sender` before calling the pool. Alternatively, the router can embed the real user address in `extensionData` and the extension can decode it — but this requires a coordinated convention between router and extension. The cleanest fix is an explicit `sender` field in the pool's swap interface so the allowlist always sees the economically relevant actor regardless of which periphery path is used.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` registered as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. Unauthorized `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient=userB, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully trades in a pool they were never authorized to access. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
