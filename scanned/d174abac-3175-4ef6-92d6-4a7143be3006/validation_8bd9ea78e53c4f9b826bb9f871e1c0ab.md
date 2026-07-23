### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the value forwarded by the pool from its own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore gates on the router's address rather than the actual trader's identity. If the router is allowlisted (which is required for any router-mediated swap to succeed on a curated pool), every user in the system can bypass the allowlist by routing through the router.

---

### Finding Description

**Hook argument binding — wrong actor**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

The pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The bypass:**

A pool admin who wants to restrict swaps to a curated set of addresses must also allowlist the router if any of those users are expected to use the standard periphery interface. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the actual end user is. Any address in the system can call `exactInputSingle` and the extension will pass.

The admin has no way to simultaneously:
1. Allow router-mediated swaps for legitimate users, and
2. Restrict which end users can trade.

Allowlisting the router collapses the per-user gate into an all-or-nothing gate on the router contract itself.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) is fully bypassed. Any unprivileged user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This constitutes a direct loss of the pool's intended access-control invariant and exposes LP funds to trades from actors the pool admin explicitly intended to exclude. Because the pool is oracle-anchored, an unauthorized trader can extract value at the oracle price without the LP having any recourse.

Severity: **High** — complete bypass of a configured access-control guard with direct fund-impacting consequences for LP principals.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery swap interface; end users are expected to use it.
- A pool admin who configures `SwapAllowlistExtension` and wants legitimate users to use the router must allowlist the router address — this is the only way router-mediated swaps can pass the hook.
- Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no flash loans, and no multi-transaction setup.
- The admin has no on-chain mechanism to distinguish which end user is behind a router call.

---

### Recommendation

The `sender` value forwarded to extensions must represent the economically relevant actor — the end user — not the immediate `msg.sender` of the pool. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should pass the original `msg.sender` (the end user) as a verified field inside `extensionData` or via a dedicated router-identity mechanism, and the extension should read from that field after verifying the caller is a trusted router.

2. **Extension-side:** `SwapAllowlistExtension` should accept an optional "true sender" override from `extensionData` when the immediate `sender` is a known trusted router, falling back to `sender` for direct pool calls. The pool factory or extension admin should maintain a registry of trusted routers.

The simplest correct fix is for the pool to expose the original initiator through a verified channel (e.g., transient storage written by the router before calling the pool and readable by extensions), so the allowlist can always gate on the true end user regardless of the call path.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured in beforeSwap slot
  admin allowlists router: allowedSwapper[pool][router] = true
  admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: X,
    ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true → PASSES
      → swap executes, attacker receives tokens

Result:
  attacker successfully swaps on a pool that was configured to block them.
  The allowlist check passed because it evaluated the router's address,
  not the attacker's address.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

```

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
