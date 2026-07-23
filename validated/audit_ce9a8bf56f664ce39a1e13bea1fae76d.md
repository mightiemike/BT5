Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router contract address, not the end user. Any pool admin who allowlists the router to permit their whitelisted users to trade via the standard interface simultaneously grants every unprivileged user on the network the ability to bypass the allowlist with a single router call.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmPool.swap` passes its own `msg.sender` (the immediate pool caller) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged into the extension call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` from the pool's perspective:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The router passes `params.extensionData` (user-supplied) to the pool, but the extension ignores `extensionData` entirely — it only reads `sender`. There is no mechanism in the router to encode the original `msg.sender` into `extensionData`, and no mechanism in the extension to decode or verify it.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the standard router at all.
- **Allowlist the router** → every address on the network can bypass the allowlist by routing through it.

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted counterparties loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade against the pool's LP reserves. The pool receives the wrong counterparty's tokens and pays out LP-owned tokens to an actor the allowlist was designed to exclude. This is direct unauthorized access to LP principal, bounded only by available liquidity and attacker capital. This meets the Sherlock threshold for High severity: broken core pool functionality causing loss of funds and unauthorized swap execution against LP assets.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` pool and wants their allowlisted users to use the standard router will inevitably allowlist the router address — this is the only way to make the router work for legitimate users. The bypass requires no special privileges, no flash loans, no multi-block setup, and no unusual token behavior. A single `exactInputSingle` call from any EOA suffices. The condition (router allowlisted) is a near-certain operational state for any pool that intends to be usable.

## Recommendation

The extension must check the economic actor, not the immediate pool caller. The cleanest fix is for `MetricOmmSimpleRouter` to ABI-encode the original `msg.sender` into `extensionData` before calling `pool.swap()`, and for `SwapAllowlistExtension.beforeSwap` to decode and verify it when `sender` is a known router. Alternatively, redesign the extension to accept a signed user identity in `extensionData` that the router forwards from its own `msg.sender`, so the checked identity is always the economic actor regardless of routing path. As a documentation-level mitigation, `SwapAllowlistExtension` should explicitly state it is incompatible with the public router unless the router encodes caller identity.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension wired as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     (necessary so that allowlisted users can use the router).
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         extensionData: ""
     })
  5. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router.
  6. Pool calls _beforeSwap(router, attacker, ...).
  7. Extension checks allowedSwapper[pool][router] → true → no revert.
  8. Swap executes; attacker receives LP-owned token1 without being on the allowlist.

Result: allowlist guard silently fails open for every user who routes through
        MetricOmmSimpleRouter, regardless of individual allowlist status.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```
