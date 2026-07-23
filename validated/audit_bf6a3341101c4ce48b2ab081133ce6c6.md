### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender = router`. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (a natural and expected action), every unpermissioned user can bypass the per-user swap restriction by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool's `msg.sender` is the router.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. The extension evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end user).

The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Exact corrupted value:** The identity checked against the allowlist is the router contract address, not the EOA or contract that initiated the swap.

**Two broken states:**

| Router allowlisted? | Result |
|---|---|
| Yes | Every user bypasses the per-user allowlist via the router |
| No | Every individually-allowlisted user is blocked from using the router |

The first state is the fund-impacting one. A pool admin who wants a private pool will naturally allowlist the router as a trusted periphery contract, inadvertently opening the pool to all users.

The same misbinding applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput` — in all cases the pool's `msg.sender` is the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with a curated LP set) is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized swaps drain LP assets at oracle-derived prices, causing direct loss of LP principal and breaking the pool's core access invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gates.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point; virtually all non-technical users will route through it.
- Allowlisting the router is the obvious and expected admin action to "enable router users" on a restricted pool — the admin has no indication this collapses per-user gating.
- No special privileges, flash loans, or unusual token behavior are required. Any EOA can call `exactInputSingle`.
- The bypass is deterministic and requires a single transaction.

---

### Recommendation

The extension must gate on the economically relevant actor — the end user — not the intermediary. Two options:

**Option A (preferred):** Pass `recipient` (the swap beneficiary) instead of `sender` to the allowlist check, or add a separate `originator` field to the `beforeSwap` hook that the router populates via `extensionData`.

**Option B:** Inside `SwapAllowlistExtension.beforeSwap`, decode the actual user from `extensionData` when `sender` is a known router, and check that address. This requires a convention between the router and the extension.

**Option C (minimal):** Document that `sender` is the direct pool caller (not the end user) and require pools using `SwapAllowlistExtension` to allowlist only EOAs that call the pool directly, never the router. This breaks router usability for restricted pools.

The cleanest fix is for the pool to pass `tx.origin` or for the router to embed the originating user in `extensionData` and for the extension to decode it — but `tx.origin` is unsafe in a general context. The proper solution is an explicit originator field in the hook interface.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - pool.swap(attacker, true, X, ...) is called with msg.sender = router
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; attacker receives output tokens
  - Per-user allowlist is completely bypassed
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
