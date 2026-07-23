### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end-user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the per-user allowlist by calling the same router.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router is `msg.sender` to the pool, so `sender` = router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **Every** user, including non-allowlisted ones, can swap through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The `DepositAllowlistExtension` does not share this flaw because it gates on the explicit `owner` parameter, which the liquidity adder passes correctly regardless of who the payer (`msg.sender`) is: [4](#0-3) 

---

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the pool admin has allowlisted the router (a necessary step to support router-mediated swaps for legitimate users). The bypass is complete and unconditional: the extension sees only the router address and cannot distinguish callers. This breaks the core curation invariant of the `SwapAllowlistExtension` and allows unauthorized users to trade against a pool that was designed to be restricted, directly impacting LP funds through unwanted price exposure and fee dilution.

---

### Likelihood Explanation

The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exact*` on a pool that has the `SwapAllowlistExtension` configured. The only precondition is that the pool admin has allowlisted the router, which is the natural and expected action for any pool that wants to support the standard periphery. The pool admin's own correct configuration is what opens the bypass. [5](#0-4) 

---

### Recommendation

The extension must recover the true end-user identity. Two approaches:

1. **Pass the original caller through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of `sender`. This requires a trusted forwarding convention.

2. **Check `tx.origin` as a fallback** (weaker, not recommended for general use): Only acceptable if the pool is designed for EOA-only access.

3. **Preferred — router-aware identity resolution**: Add a standard interface that the router implements to expose the originating user, and have the extension call it when `sender` is a known router. This keeps the extension stateless and the router accountable.

The invariant that must hold: the identity checked by `SwapAllowlistExtension` must be the same actor that the pool admin intended to gate, regardless of which supported public entrypoint reaches the pool.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for legitimate users.
3. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(...)` — `msg.sender` to the pool is the router.
5. `_beforeSwap` passes `sender = router` to the extension.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. The swap executes successfully despite the attacker never being individually allowlisted.

The existing integration test `test_allowedSwapSucceeds` in `FullMetricExtension.t.sol` allowlists `address(callers[0])` (a `TestCaller` contract acting as the direct pool caller), which mirrors the router pattern and confirms the extension only ever sees the intermediary's address: [6](#0-5)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
