### Title
`SwapAllowlistExtension` checks router address instead of original user — allowlist bypassed for any user routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes in, which equals `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, that `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router address to enable router-mediated swaps for their allowlisted users inadvertently opens the gate to every user on the network, because the extension cannot distinguish between an allowlisted user routing through the router and a completely unauthorized user doing the same.

---

### Finding Description

**Identity substitution in the swap path**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that identity against its per-pool allowlist, using `msg.sender` (the pool) as the pool key and `sender` (the direct caller of `pool.swap()`) as the swapper key: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`, so `sender` delivered to the extension is always the router address, never the originating EOA.

**The forced admin dilemma**

A pool admin who deploys a `SwapAllowlistExtension`-guarded pool and wants allowlisted users to be able to trade through the router must call `setAllowedToSwap(pool, router, true)`. The moment they do, the check `allowedSwapper[pool][router]` returns `true` for every swap that arrives through the router, regardless of who initiated it. There is no mechanism in the extension or the router to recover the original user's identity; `extensionData` is forwarded opaquely but `SwapAllowlistExtension` never reads it. [4](#0-3) 

The admin cannot simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from using the router. The two goals are mutually exclusive given the current implementation.

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (KYC'd users, institutional partners, whitelisted bots) and also allowlists the router to support normal UX loses its access control entirely for the router path. Non-allowlisted users can execute full swaps against the pool's LP liquidity at oracle prices. Because the pool is oracle-driven, the LP does not receive a worse price per trade, but the pool's design intent — restricting who may trade — is completely broken. If the restriction existed to prevent adversarial flow (e.g., informed traders, sandwich bots, or regulatory non-compliant counterparties), those actors can now freely drain the pool's liquidity. This is a broken core pool functionality / admin-boundary break with direct LP exposure.

---

### Likelihood Explanation

The trigger is a non-malicious, operationally expected admin action: allowlisting the router so that allowlisted users can trade through the standard periphery. Any pool operator who reads the `SwapAllowlistExtension` docs and concludes "I need to allowlist the router for my users" will unknowingly open the gate. The router is a public, permissionless contract; once the router address is allowlisted, every user on the network can exploit the bypass in the same transaction. No front-running, flash loans, or special privileges are required.

---

### Recommendation

The router should encode the originating user's address into `extensionData` (or a dedicated field) so that the extension can verify the real swapper. Alternatively, `SwapAllowlistExtension` should accept an optional `trustedForwarder` list: when `sender` is a known forwarder, the extension decodes the real user from `extensionData` and checks that address instead. A simpler short-term fix is to document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user gating is only enforceable on direct pool calls.

---

### Proof of Concept

```solidity
// Setup: pool guarded by SwapAllowlistExtension
// Admin allowlists alice (intended user) and the router (to let alice use the router)
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);

// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] == true  ✓
// bob's swap executes successfully despite not being on the allowlist
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        tokenOut:        address(token0),
        zeroForOne:      false,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       bob,
        deadline:        block.timestamp + 1,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// bob receives token0 from the restricted pool — allowlist fully bypassed
vm.stopPrank();
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which checks `allowedSwapper[msg.sender][sender]` where `sender` is always the router when the periphery is used, not the originating EOA. [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
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
