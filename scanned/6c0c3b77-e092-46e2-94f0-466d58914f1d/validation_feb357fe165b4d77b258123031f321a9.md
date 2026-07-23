### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The allowlist therefore checks the router's address, not the real swapper's identity. If the router is allowlisted (or `allowAll` is set for the router address), every user on-chain can bypass the swap allowlist entirely.

---

### Finding Description

The `ExtensionCalling._beforeSwap` dispatcher forwards `sender` — the pool's `msg.sender` — verbatim to every configured extension: [1](#0-0) 

When `MetricOmmSimpleRouter` calls `pool.swap(recipient, zeroForOne, amountSpecified, ...)`, the pool's `msg.sender` is the router contract. The pool passes that address as `sender` to `_beforeSwap`, which encodes it into the `beforeSwap` call dispatched to every extension. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[pool][sender]` — keyed on the router address, not the real user. The unit test for the extension confirms the first positional argument is the identity that is checked: [3](#0-2) 

A pool admin who intends to restrict swaps to a curated set of addresses (KYC, institutional, etc.) will allowlist individual user addresses. None of those addresses match the router, so router-mediated swaps revert — breaking the intended UX. To restore router access the admin must allowlist the router itself. Once the router is allowlisted, the `allowedSwapper` check degenerates: **any** caller of the public router passes, because the router is always the `sender` the extension sees.

The research pivot in the repository's own audit scaffold confirms this is the intended attack surface:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

This is the direct analog of the `protectedTokens()` bug in the seed report: the wrong identity is placed in the guard array (`router` instead of `user`), so the guard is structurally bypassed for every router-mediated swap.

---

### Impact Explanation

**Critical / High.** A pool configured with `SwapAllowlistExtension` to enforce access control (e.g., permissioned liquidity, regulatory compliance, private market-making pools) is fully open to any on-chain address that calls `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may trade" — is broken. Unauthorized users can drain one-sided liquidity, extract arbitrage, or interact with pools they are explicitly excluded from, causing direct loss of LP principal and fee revenue.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary public entry point for swaps: [5](#0-4) 

Any pool that uses `SwapAllowlistExtension` and whose admin allowlists the router (the only way to make the router work with the allowlist) is immediately vulnerable. The trigger requires no special privilege — any EOA calls the public router.

---

### Recommendation

Pass the **original user's address** through the swap call chain rather than relying on `msg.sender` at the pool boundary. Two standard approaches:

1. **Router forwards caller identity**: The router encodes `msg.sender` into `extensionData` and the extension reads it from there, verifying the pool is the actual `msg.sender` (so the data cannot be spoofed by a direct caller).
2. **Pool exposes a `sender` override**: The pool's `swap()` accepts an explicit `sender` parameter that the router populates with `msg.sender` before forwarding; the pool validates that `msg.sender == router` before trusting the override.

Either approach ensures the allowlist checks the economically relevant actor — the wallet initiating the trade — rather than the intermediary router contract.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — the only way to allow router-mediated swaps.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
4. Attacker calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
5. Router calls pool.swap(attacker_as_recipient, ...).
6. Pool calls _beforeSwap(router_address, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
8. Swap executes. Attacker receives output tokens.
9. The allowlist never checked attacker's address; the guard is bypassed.
```

The `sender` the extension receives is always the router address, confirmed by the extension interface and dispatcher: [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L32-38)
```text
  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
