### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` as `sender`, so when `MetricOmmSimpleRouter` calls `pool.swap()`, the extension sees the router's address — not the end user's address. If the pool admin allowlists the router (the only way to let legitimate users reach the pool through the router), every unprivileged user can bypass the allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist, using `msg.sender` (the pool) as the namespace key:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

At this point `msg.sender` inside the pool is the **router**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → all router-mediated swaps revert, including those from legitimate users.
- **Allowlist the router** → every user, regardless of allowlist status, can bypass the gate by routing through the public `MetricOmmSimpleRouter`.

There is no configuration that simultaneously permits legitimate router usage and enforces per-user restrictions.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for curated pools — pools restricted to KYC'd counterparties, institutional participants, or other access-controlled groups. Bypassing it means:

- Any unprivileged address can execute swaps on a pool the admin intended to restrict.
- LP funds are exposed to swappers the pool admin explicitly excluded.
- The admin-configured access boundary is silently nullified by a public periphery path.

This satisfies the **admin-boundary break** impact class: a factory/pool admin access control is bypassed by an unprivileged path (`MetricOmmSimpleRouter` is a public, permissionless contract).

---

### Likelihood Explanation

The bypass is reachable whenever the pool admin allowlists the router — a natural and expected action for any pool that wants to support the standard periphery UX. The router is a public contract with no access control of its own. No special privilege, flash loan, or multi-step setup is required; a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the **economic actor** (the end user who benefits from the swap), not the **call-chain intermediary** (the router). Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `tx.origin` as a fallback identity**: Acceptable only if the threat model excludes contract-based callers; otherwise it introduces its own bypass surface.
3. **Document that allowlisting the router is equivalent to `allowAllSwappers = true`** and enforce this invariant in the admin tooling so pool admins cannot accidentally create a false sense of restriction.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  admin calls swapExtension.setAllowedToSwap(pool, router, true)  // enable router UX

Attack (bob is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓
    → swap executes successfully for bob

Result: bob, who is not on the allowlist, completes a swap on a curated pool.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
