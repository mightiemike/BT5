### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the **router's address**, not the actual user's address. If the pool admin allowlists the router so that legitimate users can trade through it, every unpermissioned user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. The pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument delivered to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

So `pool.swap()` sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — DoS on the supported periphery path |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the guard by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**High.** The swap allowlist is the primary access-control mechanism for curated pools (e.g., KYC-gated, institution-only, or market-maker-only pools). A non-allowlisted user who routes through `MetricOmmSimpleRouter` bypasses the guard entirely and executes swaps against pool liquidity that was never intended to be accessible to them. Because the router is a public, permissionless contract, the bypass requires no special privilege — any address can call it.

---

### Likelihood Explanation

**High.** The router is the canonical, documented swap entry point for end users. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which immediately opens the bypass to all users. The attack path is a single public function call with no preconditions beyond knowing the pool address.

---

### Recommendation

The extension must gate on the **actual user**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and verifies it, trusting only a known router address as the source. This requires a trusted-router registry in the extension.

2. **Redesign the hook signature**: Add an explicit `originalCaller` field to the `beforeSwap` hook interface so the pool can propagate the true initiator. The router would pass `msg.sender` as `originalCaller` when calling `pool.swap`, and the pool would forward it to the extension.

The simplest near-term mitigation is option 1: the extension checks whether `sender` is a known trusted router, and if so, decodes the real user from `extensionData` before performing the allowlist lookup.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice] = true
  pool admin: allowedSwapper[pool][router] = true   ← required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: WETH, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, ...)   // msg.sender = router
      → _beforeSwap(msg.sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives output tokens

Result:
  bob bypasses the allowlist and trades on a curated pool.
  The extension never checked bob's address.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
