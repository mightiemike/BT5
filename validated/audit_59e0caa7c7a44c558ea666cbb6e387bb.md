Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to its own `msg.sender`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-based swap on an allowlisted pool), every unpermissioned user can bypass the swap allowlist by routing through the router, fully breaking the pool's access-control invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The dilemma is inescapable: if the admin does not allowlist the router, allowlisted users cannot use the canonical swap interface. If the admin does allowlist the router, `allowedSwapper[pool][router] = true` and every user on the network can route through the router to bypass the check — the extension checks the router's allowlist entry, not the attacker's.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties or institutional LPs). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. The pool's access-control invariant is fully broken for all router-mediated paths. LPs who deposited into a restricted pool under the assumption that only trusted counterparties could trade against their liquidity are exposed to unrestricted public swap flow, constituting a broken core pool functionality with direct fund-exposure impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed swap interface for the protocol. Any user who discovers the allowlist restriction can trivially route through the router instead of calling the pool directly. No privileged access, special tokens, or malicious setup is required — a standard `exactInputSingle` call suffices. The pool admin enabling router-based swaps for their allowlisted users is the expected operational pattern, making the router-allowlist entry the natural configuration path.

## Recommendation
The `SwapAllowlistExtension` must gate on the end user's identity, not the intermediary's. The cleanest fix is to extend the `beforeSwap` hook or `extensionData` convention so the router forwards the original `msg.sender` in a verifiable way (e.g., a trusted forwarder pattern), and have the extension verify that field. Alternatively, document and enforce at the factory level that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly — but this eliminates router usability for restricted pools entirely.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedUser` is allowlisted.
// Admin also allowlists the router so trustedUser can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), trustedUser, true);

// Attack: attacker (not allowlisted) routes through the router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: attacker,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed.
// The extension checked allowedSwapper[pool][router] = true, not allowedSwapper[pool][attacker].
```

The extension checks `allowedSwapper[pool][router]` (true) rather than `allowedSwapper[pool][attacker]` (false), so the guard passes and the attacker swaps on a pool they were never permitted to access.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
