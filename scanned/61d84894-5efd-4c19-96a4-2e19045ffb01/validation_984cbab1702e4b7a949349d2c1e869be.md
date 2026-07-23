### Title
`SwapAllowlistExtension` gates the router address instead of the real end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (the natural step to permit router-mediated swaps), every unprivileged address can bypass the allowlist by calling the router.

---

### Finding Description

**Call chain:**

```
User (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
          → _beforeSwap(msg.sender=router, recipient, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

Inside `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user. The check `allowedSwapper[pool][router]` passes for every user who routes through the router, because the router is a single shared contract.

The pool correctly passes `msg.sender` as `sender` to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
``` [2](#0-1) 

The router never forwards the original user's address; it calls `pool.swap()` directly with no identity-forwarding mechanism:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [3](#0-2) 

**Two broken states result:**

| Pool admin action | Effect |
|---|---|
| Allowlists the router | Every user on-chain can swap; allowlist is fully defeated |
| Does not allowlist the router | Even allowlisted users cannot swap through the router |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — the only way to let legitimate users use the standard periphery — the gate is open to every address. Unauthorized swappers can:

- Extract value from LP positions at oracle-derived prices the pool admin did not intend to offer them.
- Drain one side of the pool if the oracle price diverges from market, causing direct LP principal loss.

This is a broken core pool functionality with direct loss of user principal (LP assets), matching the allowed impact gate.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented swap entry point for end users.
- Any pool that enables `SwapAllowlistExtension` and wants users to swap through the router must allowlist the router, triggering the bypass automatically.
- No special privilege, flash loan, or unusual token behavior is required — a plain `exactInputSingle` call suffices.
- The attacker needs only to observe that the router is allowlisted (readable on-chain from `allowedSwapper`) and call the router.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `payer`/`originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that address. This requires a coordinated router + extension change.

2. **Check `recipient` instead of (or in addition to) `sender`.** For swap allowlists the economically relevant identity is often the recipient of output tokens. The extension already receives `recipient` as its second argument but currently ignores it.

3. **Document that the extension is incompatible with shared routers.** If neither fix is applied, the NatSpec must warn that allowlisting any shared intermediary defeats the gate.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner explicitly passed by the caller) rather than `sender` (the intermediary), which is the correct pattern to follow. [4](#0-3) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - Bob (not allowlisted) calls:
      router.exactInputSingle({
          pool:      pool,
          recipient: bob,
          zeroForOne: true,
          amountIn:  X,
          ...
      })
  - Router calls pool.swap(bob, true, X, ...)  with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes; allowlist is bypassed.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; Bob receives output tokens.
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
