### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` on the pool, so the pool forwards the router's address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. Any user can bypass a per-user swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the address that called `pool.swap()`: [3](#0-2) 

When a user calls the router, the router calls `pool.swap()`, making the router the `msg.sender` inside the pool. The pool therefore passes the **router's address** as `sender` to the extension. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert, even for allowlisted users |
| Yes | Every user on the network can swap by routing through the router — allowlist is nullified |

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the position owner), not `sender`, so the deposit allowlist correctly gates the economic actor regardless of who calls `addLiquidity`. [4](#0-3) 

The asymmetry confirms the swap path has a wrong-actor binding bug.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., for KYC'd counterparties, institutional LPs, or a controlled market-making arrangement) and attaches `SwapAllowlistExtension` receives no protection once the router is allowlisted. Any unprivileged user can call the public router and execute swaps against the pool's LP liquidity. LP funds are exposed to toxic flow or unauthorized counterparties that the allowlist was designed to exclude. This is a direct loss-of-principal risk for LPs on curated pools.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery entrypoint for swaps. Any user who reads the protocol docs will use it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only a standard router call. The pool admin must allowlist the router to make the pool usable through the router at all, which automatically opens the bypass.

---

### Recommendation

The pool should pass the **originating user** as `sender`, not `msg.sender`. Two approaches:

1. **Router forwards the real sender**: Have `MetricOmmSimpleRouter` accept a `sender` parameter and pass it as `callbackData` or a dedicated field, and have the pool use that value when calling extensions. This requires a protocol-level convention.

2. **Extension reads `recipient` instead of `sender`**: For swap allowlists, gate on `recipient` (the address receiving output tokens) rather than `sender`. This is already available as the second argument to `beforeSwap` and correctly identifies the economic beneficiary regardless of routing.

3. **Minimal fix**: Document that `SwapAllowlistExtension` is incompatible with the router and must only be used with direct pool calls, and add a revert in the extension if `msg.sender` (the pool) is called from a known router.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.

// Direct swap by attacker — correctly reverts:
vm.prank(attacker);
pool.swap(attacker, false, 1000, type(uint128).max, "", "");
// → reverts NotAllowedToSwap ✓

// Pool admin must allowlist the router to let allowedUser use it:
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Now attacker routes through the router — passes the allowlist check:
vm.prank(attacker);
router.exactInput(...); // calls pool.swap() with msg.sender = router
// → succeeds, attacker swaps on a pool they should be barred from ✗
```

The extension checks `allowedSwapper[pool][router]` which is `true`, so the attacker's swap executes against LP liquidity that the allowlist was meant to protect.

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
