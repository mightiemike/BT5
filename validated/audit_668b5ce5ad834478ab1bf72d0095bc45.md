### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` at the pool call site. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router so that legitimate users can reach the pool through the standard periphery path, every unprivileged user can bypass the allowlist by routing through the same contract.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` — wrong actor binding**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool populates `sender` with its own `msg.sender` and forwards it verbatim to the extension dispatcher:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end-user
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this value unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The impossible configuration**: A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces a binary choice with no correct option:

| Router allowlisted? | Allowlisted user via router | Disallowed user via router |
|---|---|---|
| No | **Blocked** (broken UX) | Blocked |
| Yes | Allowed | **Allowed** (bypass) |

Allowlisting the router — the natural step to let legitimate users reach the pool through the standard periphery — simultaneously grants every unprivileged address the ability to bypass the allowlist entirely.

---

### Impact Explanation

Any user blocked by the `SwapAllowlistExtension` can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) on a pool where the router has been allowlisted. The extension sees `sender = router`, which passes the check. The unauthorized swap executes at the oracle-anchored bid/ask price, consuming LP liquidity and generating output tokens for the attacker. LP principal is directly at risk on every curated pool that relies on the allowlist as its sole access-control layer.

---

### Likelihood Explanation

The router is the canonical user-facing swap entry point documented in the protocol. A pool admin who wants to restrict trading to a curated set of users but still allow those users to use the standard router will naturally allowlist the router address. The bypass is then reachable by any unprivileged address with no special setup, no privileged role, and no non-standard token behavior.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end-user — not the intermediary contract. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of `sender` for router flows, or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the pool configuration level.

The cleanest fix is to redesign the hook interface so that the pool passes both the direct caller (`sender`) and an optional `origin` (the transaction originator or a router-attested user identity), and have `SwapAllowlistExtension` check `origin` when present.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  3. Admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  4. bob (not allowlisted) calls:
       router.exactInputSingle({
           pool: pool,
           tokenIn: token0,
           zeroForOne: true,
           amountIn: X,
           amountOutMinimum: 0,
           ...
       })

  5. router calls pool.swap(recipient=bob, ...)
     → pool.msg.sender = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
     → swap executes; bob receives token1 output

Result: bob bypasses the allowlist, draining LP token1 at the oracle-anchored ask price.
``` [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
