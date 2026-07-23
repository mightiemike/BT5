### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the **router's address**, not the user's address. A pool admin who allowlists the router so that legitimate users can trade through it inadvertently opens the gate to every non-allowlisted user, completely defeating the curation policy.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`: [2](#0-1) 

`_beforeSwap` is called from `MetricOmmPool.swap` with `msg.sender` of the pool call as `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`. At that point `msg.sender` to the pool is the **router contract**, so `sender` forwarded to the extension is the router address — not the end user.

This creates an inescapable dilemma for any pool admin who wants to support router-mediated swaps on a curated pool:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate allowlisted users cannot trade through the router at all |
| **Allowlist the router** | Every non-allowlisted user can bypass the gate by routing through the router |

The second branch is the exploit path. Once the router is allowlisted (the only way to make the router usable for legitimate users), `allowedSwapper[pool][router] == true`, and any caller of `MetricOmmSimpleRouter` passes the check regardless of their own address.

The protocol's own research document explicitly identifies this as the intended attack surface: [4](#0-3) 

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."

---

### Impact Explanation

**Severity: High**

A pool configured with `SwapAllowlistExtension` (e.g., a KYC-gated or institutional pool) can be fully bypassed by any user who calls `MetricOmmSimpleRouter`. The non-allowlisted user executes swaps at oracle-anchored prices, draining liquidity from a pool that was intended to be restricted. LP principal is at risk because the pool's curation invariant — that only approved counterparties trade against its liquidity — is broken. This is a direct loss-of-policy impact with fund consequences on every curated pool that supports router access.

---

### Likelihood Explanation

**Likelihood: High**

The router is the standard, documented user-facing entry point for swaps. Any pool admin who deploys a curated pool and wants to support normal user tooling (wallets, aggregators routing through `MetricOmmSimpleRouter`) must allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. The condition that triggers it (router allowlisted) is the expected production configuration for any curated pool that is not purely direct-call-only.

---

### Recommendation

The extension must resolve the **actual end-user identity** rather than the immediate `msg.sender` of the pool call. Two sound approaches:

1. **Pass the original caller through the router**: Have `MetricOmmSimpleRouter` forward the real user address in `extensionData`, and have `SwapAllowlistExtension` decode and check that address. This requires a trusted forwarding convention.

2. **Check `recipient` instead of `sender`**: If the pool's swap design guarantees that the economic beneficiary is `recipient`, gate on `recipient`. This is only correct if the pool enforces that `recipient` cannot be set to an arbitrary address by an untrusted router.

3. **Preferred — check both `sender` and `recipient`**: Require that both the router (`sender`) and the end user (`recipient`) are allowlisted, so the router being allowlisted does not alone open the gate.

The cleanest fix consistent with the external report's recommendation ("use a single canonical identity at the token boundary") is to key the allowlist on `recipient` — the address that actually receives the output tokens — since that is the economically relevant actor the pool admin intends to gate.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can trade via the router.
3. Non-allowlisted attacker (address NOT in allowedSwapper[pool]) calls:
     MetricOmmSimpleRouter.exactInputSingle(pool, zeroForOne, amountIn, ...)
4. Router calls pool.swap(recipient=attacker, ...).
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Swap executes. Attacker receives output tokens from a pool they were
   never authorized to trade against.
```

The check `allowedSwapper[msg.sender][sender]` at line 37 of `SwapAllowlistExtension.sol` resolves to `allowedSwapper[pool][router]`, which is `true` by the admin's necessary configuration, so the revert is never reached. [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L301-360)
```text
  }

  // ============ External: swap simulation ============

  /// @inheritdoc IMetricOmmPoolActions
  function simulateSwapAndRevert(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
    if (bidPriceX64 >= askPriceX64) revert BidGreaterThanAsk();
    if (bidPriceX64 == 0) revert BidIsZero();

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

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

    (uint256 midPriceX64, uint256 baseFeeX64) = SwapMath.midAndSpreadFeeX64FromBidAsk(bidPriceX64, askPriceX64);

    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );

    _nonReentrantAfter();
    revert SimulateSwap(amount0Delta, amount1Delta);
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
