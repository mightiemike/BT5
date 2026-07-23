Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` enforces its allowlist against `sender`, which is the immediate `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router address, not the end user. A pool admin who allowlists the router inadvertently opens the allowlist to every user on the network, because the extension cannot distinguish individual end users once the router is the caller.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value the pool forwarded from its own `msg.sender`. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The `extensionData` field is caller-controlled and is not used by the extension for identity checks, so it cannot close this gap. [4](#0-3) 

## Impact Explanation
A curated pool (e.g., KYC-only, institutional-only, or regulatory-restricted) deploying `SwapAllowlistExtension` must allowlist the router to support normal UX. Once the router is allowlisted, any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and execute a swap that the allowlist was supposed to block. The pool's curated access control is silently voided for all router-mediated flows. This constitutes broken core pool functionality — the allowlist guard fails open — and represents unauthorized access to restricted pools with direct fund-flow consequences. Severity: **High**. [5](#0-4) 

## Likelihood Explanation
- The router is a standard, publicly deployed periphery contract.
- Any pool that wants to support normal user UX must allowlist the router.
- The bypass requires zero privilege: any EOA can call `exactInputSingle`.
- The pool admin has no on-chain signal that the allowlist is being bypassed; the extension simply sees an allowlisted address (the router).

**Likelihood**: High — the bypass is trivially reachable by any user the moment the router is allowlisted, which is the expected operational state for any pool that supports periphery access. [6](#0-5) 

## Recommendation
The extension must gate on the economic actor, not the immediate caller. Two options:

1. **Pass end-user identity through `extensionData` and verify it with a signature or trusted forwarder pattern.** The router would include the original `msg.sender` in `extensionData`; the extension would verify a signature or check a trusted-forwarder registry before accepting it.
2. **Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the router to attest the real user via a separate trusted channel** (e.g., a signed message included in `extensionData`).

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if called from any router address — but this breaks composability. The correct fix is option 1 or 2 above. [7](#0-6) 

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  - attacker (non-allowlisted EOA) calls:
      router.exactInputSingle({
          pool: curated_pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - router calls pool.swap(recipient, zeroForOne, ...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks: allowedSwapper[pool][router] == true  ✓
  - swap executes; attacker receives output tokens

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool.
  - The allowlist guard is completely bypassed.
``` [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
