### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore gates the router's address rather than the real swapper. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the network, completely defeating the curation policy.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._callExtensionsInOrder(
                       IMetricOmmExtensions.beforeSwap(sender=router, ...)
                     )
                          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                               checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender` — the router: [3](#0-2) 

The router calls `pool.swap` with no mechanism to forward the original user's address into the `sender` slot: [4](#0-3) 

**Two broken invariants result:**

1. **Allowlist bypass (primary impact):** If the pool admin allowlists the router address so that router-mediated swaps are possible, every user on the network passes the check — the extension sees `sender = router`, which is allowlisted, regardless of who called the router. The curation policy is completely nullified.

2. **Allowlisted users locked out of the router (secondary impact):** If the pool admin allowlists only specific user EOAs (not the router), those users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts `NotAllowedToSwap`. They must implement `IMetricOmmSwapCallback` themselves and call the pool directly, which is not a realistic expectation for ordinary users.

The `DepositAllowlistExtension` does not share this flaw because it explicitly checks the `owner` argument (second parameter), which the liquidity adder correctly forwards as the position owner: [5](#0-4) 

The swap extension has no equivalent correct binding.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed the moment the pool admin allowlists the router to support normal user flows. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool, receiving output tokens and draining LP assets at oracle prices. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and also wants users to be able to use the router must allowlist the router — at which point the bypass is unconditional and requires no special setup by the attacker. The trigger is a single public call to `exactInputSingle` with any `extensionData`.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and verifies it, and also verifies that `sender` (the pool's `msg.sender`) is a trusted router registered with the factory. This preserves the operator pattern without exposing the bypass.

2. **Separate router-level allowlist from pool-level allowlist:** The factory tracks approved routers; the extension checks `allowedSwapper[pool][sender]` only when `sender` is not a factory-approved router, and in the router case decodes the real user from `extensionData`.

Either way, the extension must never treat the router's address as the identity to gate.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so normal users can swap:
//   extension.setAllowedToSwap(pool, address(router), true);
// Admin does NOT allowlist attacker:
//   extension.isAllowedToSwap(pool, attacker) == false

// Attacker bypasses the allowlist:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension sees sender = address(router), which IS allowlisted → passes.
// Attacker receives token1 output despite not being on the allowlist.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
