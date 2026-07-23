### Title
`SwapAllowlistExtension` Bypass via Router — Allowlist Gates on Router Address Instead of Actual Swapper - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the **router's address**, not the actual user's address. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user restriction by calling the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

When the user goes through the router, the router calls `pool.swap()` directly — the actual user address is stored only in transient callback context, never forwarded to the extension: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the economically relevant depositor), not on `sender` (the adder contract): [5](#0-4) 

The pool passes `owner` as a separate, user-controlled argument to `addLiquidity`, so the deposit allowlist is immune to the same router-indirection problem. No equivalent forwarding exists for the swap path.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) faces an irresolvable dilemma:

- If the router is **not** allowlisted, no allowlisted user can use the router at all.
- If the router **is** allowlisted (the only way to enable router-mediated swaps for legitimate users), every address on the network can bypass the per-user restriction by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the public router.

The attacker receives real token output from the pool and pays real token input — the swap settles fully. The pool's access-control invariant ("only allowlisted addresses may swap") is broken, and LPs in a restricted pool are exposed to counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special role, token balance, or prior interaction is required.
- The only precondition — the router being allowlisted — is a necessary operational step for any pool that wants to support router-mediated swaps for its legitimate users.
- The bypass is reachable on every swap entry point the router exposes (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

Pass the **original user** through the swap path so the extension can gate on the economically relevant actor, mirroring how the deposit allowlist uses `owner`.

Two options:

1. **Add a `swapper` parameter to `pool.swap()`** (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender` before calling the pool. The pool forwards this value as `sender` to extensions instead of its own `msg.sender`.

2. **Check `recipient` instead of `sender` in `SwapAllowlistExtension`** — the router always sets `recipient` to the user-supplied address. This is a lower-effort fix but conflates the output recipient with the access-control identity.

Option 1 is architecturally correct and consistent with how `DepositAllowlistExtension` handles the operator/owner separation.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys pool with SwapAllowlistExtension,
//        allowlists alice (trusted user) and the router (to enable router swaps).
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);
// bob is NOT allowlisted

// Attack: bob (non-allowlisted) calls the public router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           address(pool),
        tokenIn:        address(token0),
        tokenOut:       address(token1),
        zeroForOne:     true,
        amountIn:       1_000,
        amountOutMinimum: 0,
        recipient:      bob,
        deadline:       block.timestamp + 1,
        priceLimitX64:  0,
        extensionData:  ""
    })
);
// pool.swap() is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// bob's swap succeeds despite not being allowlisted
```

The pool's `beforeSwap` hook fires with `sender = address(router)`, which is allowlisted, so the guard passes and bob receives token output from a pool that was supposed to be restricted to alice only. [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
