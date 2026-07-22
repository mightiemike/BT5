### Title
`SwapAllowlistExtension` checks router address instead of end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool populates with `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the router's address — not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user, defeating the allowlist entirely.

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

The router is `msg.sender` inside the pool, so `sender` forwarded to the extension is the **router address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through that contract.

A pool admin who wants router-mediated swaps to work for their allowlisted users must allowlist the router. The moment they do, the check passes for **all** callers of the router, including addresses the admin explicitly never allowlisted.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` parameter (the economically relevant party), which the pool and the liquidity adder both populate correctly regardless of who the `msg.sender` is: [5](#0-4) 

### Impact Explanation

**High.** A pool protected by `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-internal actors, or whitelisted market makers). Once the router is allowlisted — a necessary step for any user who wants to swap via the standard periphery — the guard is completely bypassed. Any address can execute swaps against the pool's LP reserves, exposing LP principal to unauthorized counterparties and potentially enabling adversarial price impact or sandwich attacks that the allowlist was meant to prevent.

### Likelihood Explanation

**Medium.** The `SwapAllowlistExtension` is a production periphery contract. A pool admin who deploys it and also wants users to access the pool through the standard `MetricOmmSimpleRouter` will naturally allowlist the router. The bypass is non-obvious because the admin's mental model is "I allowlisted the router so my allowlisted users can use it," not "I just opened the pool to everyone." The `generate_scanned_questions.py` audit pivot explicitly flags this exact path: [6](#0-5) 

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the real caller through `extensionData`:** Have the router encode `msg.sender` into `extensionData` before calling `pool.swap()`, and have the extension decode and check that address. The pool already forwards `extensionData` unchanged to every hook.
2. **Check `recipient` as a proxy:** For single-hop exact-input swaps the recipient is often the user; however this breaks for multi-hop or contract recipients.
3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory/extension initialization level.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (necessary for any router-mediated swap to succeed)
3. Attacker (address NOT individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(...) — msg.sender inside pool = router.
5. _beforeSwap passes sender = router to SwapAllowlistExtension.
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Attacker's swap executes against LP reserves despite never being allowlisted.
```

The existing unit test `test_blocksSwapWhenSwapperNotAllowed` in `FullMetricExtension.t.sol` only tests a direct pool call (via `TestCaller`), never a router-mediated call, so the bypass is untested: [7](#0-6)

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
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
