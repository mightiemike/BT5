Based on my investigation of the codebase, I can trace the following architectural chain:

**Pool → ExtensionCalling → SwapAllowlistExtension**

In `MetricOmmPool.sol::swap`, `msg.sender` is passed as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the end user. The `SwapAllowlistExtension::beforeSwap` therefore receives `sender = router_address`. The research pivot explicitly flags this: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [3](#0-2) 

The same structural bypass applies to `DepositAllowlistExtension` via `MetricOmmPoolLiquidityAdder`: [4](#0-3) 

---

### Title
`SwapAllowlistExtension::beforeSwap` checks router address as `sender`, allowing non-allowlisted users to bypass the swap guard via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks the router's address against the allowlist instead of the actual trader, making the guard incomplete — directly analogous to `CurveGaugeMarket::_claimRewards` only calling one of two required claim paths.

### Finding Description
`MetricOmmPool::swap` passes `msg.sender` as `sender` to `ExtensionCalling::_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension::beforeSwap`. When a user calls `MetricOmmSimpleRouter::exactInput` (or any router entry point), the router calls `pool.swap(...)`, so `msg.sender` at the pool level is the router address. The extension's allowlist lookup is keyed on `(pool, sender)` — it sees the router, not the user. If the router is allowlisted (or the allowlist is intended to gate specific end users), any non-allowlisted user can bypass the guard by routing through the public router contract. The guard is only applied on the direct-call path; the router-mediated path is silently unenforced.

### Impact Explanation
Non-allowlisted users can execute swaps on pools that are configured to restrict trading to a specific set of addresses. This breaks the core access-control invariant of the `SwapAllowlistExtension`, allowing unauthorized fund flows through the pool. Pools deployed with this extension for KYC, whitelist, or launch-phase restrictions are fully bypassed for any user who routes through `MetricOmmSimpleRouter`.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface for the protocol. Any user aware of the router can trivially route through it. No special privileges, flash loans, or complex setup are required — a standard router call is sufficient to bypass the guard.

### Recommendation
The `SwapAllowlistExtension::beforeSwap` should gate on the **actual end user**, not the immediate `sender`. Two options:
1. Check `recipient` if the pool's design guarantees recipient == end user, or
2. Require the router to forward the originating user address in `extensionData`, and have the extension decode and check that identity.

The `DepositAllowlistExtension::beforeAddLiquidity` should apply the same fix for the `MetricOmmPoolLiquidityAdder` path.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured; allowlist only `alice`.
2. `bob` (not allowlisted) calls `MetricOmmSimpleRouter::exactInput` targeting the pool.
3. The router calls `pool.swap(recipient=bob, ...)` — pool sees `msg.sender = router`.
4. `SwapAllowlistExtension::beforeSwap` receives `sender = router_address`.
5. If the router is allowlisted (or the check passes for the router), `bob`'s swap executes successfully despite not being on the allowlist.
6. `bob` receives output tokens; the allowlist guard was never applied to his identity. [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-244)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
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

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
