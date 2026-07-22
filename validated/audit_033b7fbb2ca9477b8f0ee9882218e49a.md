### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for curated pools), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` directly, never forwarding the originating user's address: [4](#0-3) 

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users **cannot** swap through the router |
| **Allowlist the router** | **All** users bypass the per-user allowlist via the router |

There is no configuration that allows "only allowlisted users may use the router." The extension collapses all router users into a single identity — the router address — losing all per-user information, exactly analogous to the `fundingFees` single-variable collapse across multiple tokens in the seed report.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, which the liquidity adder preserves as the real user's address), not `sender`: [5](#0-4) 

The asymmetry confirms the swap path is the broken one.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted address can execute swaps on the curated pool, draining LP value through arbitrage or front-running that the allowlist was intended to prevent. This is a direct broken-core-functionality impact: the pool's primary access-control invariant fails open on the supported public swap path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point documented and deployed by the protocol. A pool admin who configures `SwapAllowlistExtension` and also wants to support router-mediated swaps for their allowlisted users has no choice but to add the router to the allowlist — the extension provides no other mechanism. This is a foreseeable and natural operational step. The bypass is then reachable by any unprivileged user with no special setup beyond calling the public router.

---

### Recommendation

1. **Change the checked identity in `SwapAllowlistExtension.beforeSwap`** to use the `recipient` or a user address decoded from `extensionData` rather than `sender`, so the gate applies to the economic beneficiary rather than the intermediary.
2. **Alternatively**, have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` for each hop, and update `SwapAllowlistExtension` to decode and check that address.
3. **Document clearly** that allowlisting any intermediary contract (router, multicall wrapper, etc.) grants swap access to all callers of that intermediary, so pool admins can make an informed choice.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls:
     swapExtension.setAllowedToSwap(pool, router, true)
   (intending to enable router-mediated swaps for allowlisted users)
3. Non-allowlisted EOA `attacker` calls:
     router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Pool calls _beforeSwap(sender=router, ...)
5. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
6. Swap executes for `attacker` despite attacker never being individually allowlisted.
7. allowedSwapper[pool][attacker] remains false — the check was never performed.
```

The existing test `test_blocksSwapWhenSwapperNotAllowed` in `metric-periphery/test/extensions/FullMetricExtension.t.sol` calls the pool directly (not through the router), so it does not exercise or catch this bypass path. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
