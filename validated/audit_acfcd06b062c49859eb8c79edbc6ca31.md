Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the router contract, not the originating user. The allowlist check `allowedSwapper[pool][sender]` therefore evaluates the router's address. Any non-allowlisted user can bypass the restriction by routing through the public router if the router is allowlisted (or `allowAllSwappers[pool]` is true), while allowlisted users who route through the router are incorrectly blocked.

## Finding Description

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // direct caller of the pool — the router, not the end user
    recipient, ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every extension in `BEFORE_SWAP_ORDER`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` performs its allowlist lookup as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router's address (since the router called `pool.swap()`). The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, `msg.sender` at the pool is the router: [4](#0-3) 

The existing `onlyPool` guard on the extension only verifies that the extension is called by a registered pool — it does not verify the identity of the originating user. No other guard in the call path recovers the true originator.

## Impact Explanation

A pool operator who deploys a KYC-gated or invite-only pool and attaches `SwapAllowlistExtension` intends to prevent non-allowlisted addresses from executing swaps. Because the guard evaluates the router's address instead of the originating user's address, two concrete broken invariants arise:

1. **Bypass (primary impact):** If the router is allowlisted on the pool (a natural admin action to permit "standard" routing), any non-allowlisted user can call `MetricOmmSimpleRouter.exactInput*` and the extension passes, allowing unauthorized swap settlement against LP liquidity. LP providers who deposited under the assumption that only vetted counterparties could trade against them suffer unauthorized price impact and potential value extraction — direct loss of LP principal.

2. **False block:** Allowlisted users who route through the router are incorrectly blocked because the router's address is not on the allowlist, breaking core swap functionality for legitimate users.

Both outcomes constitute broken core pool functionality and direct loss of user/LP principal above Sherlock thresholds.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- A pool admin allowlisting the router is a natural and expected configuration (the router is the standard swap entry point).
- No privileged setup, flash loans, callbacks, or multi-step exploits are required — a single `exactInputSingle` call suffices.
- The bypass is on the default happy path for any user interacting with the protocol through the router.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the originating user, not the direct pool caller. The most robust fix is to require the pool's periphery to forward the original `msg.sender` via `extensionData`, decode the true originator in the extension, and verify it — combined with a trusted-forwarder pattern so the extension can authenticate the claim. Alternatively, the extension could check `recipient` as a proxy for the economically relevant actor, though this can also be spoofed. The router must be updated to encode the originating user into `extensionData` for every swap hop.

## Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E.
  - E.allowedSwapper[P][router] = true  (admin allowlists the router for standard routing)
  - E.allowedSwapper[P][alice] = true   (alice is individually allowed)
  - E.allowedSwapper[P][bob]   = false  (bob is NOT allowed)

Attack (bob bypasses allowlist):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...) → msg.sender at P = address(router)
  3. P._beforeSwap(sender=address(router), ...) dispatched to E
  4. E checks allowedSwapper[P][address(router)] = true → passes
  5. Swap executes; bob receives tokens from the restricted pool.

Direct call (correctly blocked):
  1. bob calls P.swap(...) directly → msg.sender = bob
  2. E checks allowedSwapper[P][bob] = false → reverts with NotAllowedToSwap

Foundry test plan:
  - Deploy pool with SwapAllowlistExtension, allowlist only the router and alice.
  - Assert bob's direct swap reverts.
  - Assert bob's router-mediated swap succeeds (bypass confirmed).
  - Assert alice's router-mediated swap reverts (false block confirmed, router address checked instead of alice).
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
