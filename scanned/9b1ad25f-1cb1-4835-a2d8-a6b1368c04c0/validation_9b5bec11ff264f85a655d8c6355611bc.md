### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router's Address Instead of the End-User's Address, Allowing Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to gate swaps on curated pools by end-user identity. However, `beforeSwap` checks `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the user's address. If the pool admin allowlists the router (a natural step to support router-mediated swaps for allowlisted users), every non-allowlisted user can bypass the swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `pool.swap()` directly, `sender = user` — the check is correct. When the same user routes through `MetricOmmSimpleRouter`, `sender = router` — the check is on the router's address, not the user's.

The NatDoc states the contract "Gates `swap` by swapper address, per pool." The swapper is the end user, but the implementation gates the direct caller of `pool.swap()`. These diverge whenever an intermediary contract (the router) is in the call stack. [4](#0-3) 

The `DepositAllowlistExtension` does not share this flaw: it checks `owner`, which is an explicit parameter that the liquidity adder can set to the end user's address regardless of who calls `addLiquidity`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The admin of a curated pool cannot simultaneously:
1. Allow allowlisted users to swap through the router (by allowlisting the router address), and
2. Block non-allowlisted users from using the router.

If the admin allowlists the router to support router-mediated swaps for their approved users, every non-allowlisted address can bypass the swap gate by calling `MetricOmmSimpleRouter` instead of `pool.swap()` directly. The allowlist — the sole access-control mechanism on the swap path — is rendered ineffective for all router-mediated flows. Non-allowlisted users can trade on pools that were designed to be curated, causing unauthorized value flows through the pool.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address (a natural operational step once the admin wants their approved users to benefit from multi-hop routing). Once the router is allowlisted, the bypass is unconditional and requires no special privilege from the attacker — any EOA can call the router. The router is a public, permissionless contract.

---

### Recommendation

The `beforeSwap` hook should gate the economically relevant actor. Two options:

1. **Pass the originating user through the router**: Have `MetricOmmSimpleRouter` accept an explicit `swapper` parameter and pass it as `extensionData`, then have `SwapAllowlistExtension` decode and check that address instead of `sender`.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

Either way, the checked identity must be the address that economically benefits from the swap, not the intermediate contract that relays it.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is approved
  allowedSwapper[pool][router] = true     // admin enables router for alice
  allowedSwapper[pool][bob]   = false     // bob is NOT approved

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient=bob, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob not being allowlisted
``` [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
