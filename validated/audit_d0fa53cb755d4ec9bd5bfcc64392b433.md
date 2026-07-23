Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to its own `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-based swap to succeed), every unpermissioned user can bypass the allowlist by routing through the router, fully breaking the access-control invariant of the extension.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the direct caller of `pool.swap()` as `sender`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The dilemma for the pool admin is inescapable: if the router is not allowlisted, allowlisted users cannot use the router at all. If the router is allowlisted (the expected operational pattern), the extension checks `allowedSwapper[pool][router]` (true) for every caller, regardless of who the actual end user is. No configuration simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool. The pool's access-control invariant is fully broken for all router-mediated paths. LPs who deposited into a restricted pool under the assumption that only trusted counterparties could trade against their liquidity are exposed to unrestricted public swap flow. This constitutes broken core pool functionality causing potential loss of funds for LPs in restricted pools.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed swap interface for the protocol. Any user who discovers the allowlist restriction can trivially route through the router instead of calling the pool directly. No privileged access, special tokens, or malicious setup is required — a standard `exactInputSingle` call suffices. The pool admin enabling router-based swaps for their allowlisted users is the expected operational pattern, making the router-allowlist entry the natural configuration path that triggers the vulnerability.

## Recommendation
The extension must gate on the end user's identity, not the intermediary's. The preferred fix is to extend the `extensionData` convention so the router forwards the original `msg.sender` in a verifiable way. Concretely: add a trusted-forwarder pattern where the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that field when `sender` is a known router address. Alternatively, document and enforce (via factory-level check or NatSpec) that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly — though this severely limits usability.

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

The extension checks `allowedSwapper[pool][router]` (true) rather than `allowedSwapper[pool][attacker]` (false), so the guard passes and the attacker swaps on a pool they were never permitted to access. [5](#0-4) [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
