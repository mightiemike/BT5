Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the originating user, enabling allowlist bypass or locking out legitimate users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. This creates two broken outcomes: if the router is allowlisted, every user on the network can bypass the allowlist; if the router is not allowlisted, allowlisted users cannot use the standard periphery path at all.

## Finding Description
In `MetricOmmPool.swap`, `msg.sender` (the direct caller) is forwarded as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level. The originating user's address is stored in transient storage only for the payment callback — it is never forwarded to the extension: [3](#0-2) 

The full call path is: `User → MetricOmmSimpleRouter.exactInputSingle() → pool.swap(msg.sender=router) → _beforeSwap(sender=router) → allowedSwapper[pool][router]`. The originating user is never checked. No existing guard in the extension or pool corrects for this intermediary address substitution.

## Impact Explanation
On any pool with `SwapAllowlistExtension` configured, a pool admin faces two broken outcomes:

1. **Allowlist bypass (high impact):** If the admin allowlists the router so that legitimate users can swap through the standard periphery path, every unprivileged user can call any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and successfully swap on a pool intended to be restricted (e.g., KYC-gated, institutional-only, compliance-restricted). The extension sees `sender = router` (allowlisted) regardless of who the originating caller is. This is a direct policy failure enabling unauthorized swaps that drain LP value from curated pools.

2. **Broken core functionality (medium impact):** If the admin allowlists individual user addresses (the intended design), those users cannot swap through the router at all, because the extension checks `allowedSwapper[pool][router]` which is `false`. The only working path is a direct `pool.swap()` call requiring the user to implement `IMetricOmmSwapCallback` themselves, which is not the intended user flow.

Both outcomes meet the "Broken core pool functionality causing loss of funds or unusable swap flows" and "Admin-boundary break" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entry point in the periphery; most users will route through it rather than calling `pool.swap()` directly. A pool admin configuring `SwapAllowlistExtension` must decide whether to allowlist the router — either choice produces a broken outcome. No special privileges, flash loans, or unusual token behavior are required. Any user with a token balance and approval to the router can trigger the bypass.

## Recommendation
The extension must gate the originating user, not the intermediate router. Two concrete approaches:

1. **Pass the originating user in `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension but requires no core changes.

2. **Add an `originalSender` field to the `beforeSwap` hook interface:** The pool populates this from a transient-storage context set by the router (analogous to how the router already stores payer context in transient storage for callbacks), and the extension checks `originalSender` instead of `sender`.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; admin allowlists the router
// so that legitimate users can use the standard periphery path
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Any attacker can now bypass the allowlist:
address attacker = makeAddr("attacker");
deal(address(token0), attacker, 1e18);
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Succeeds — extension sees sender=router (allowlisted), not attacker
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: address(token0),
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// attacker successfully swapped on a pool that should have blocked them

// Alternatively: admin allowlists individual users (intended design)
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// allowedUser cannot swap through the router — extension checks allowedSwapper[pool][router] = false
// allowedUser must call pool.swap() directly and implement IMetricOmmSwapCallback
```

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
