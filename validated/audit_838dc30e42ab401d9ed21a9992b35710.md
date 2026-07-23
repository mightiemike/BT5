Based on my analysis of the codebase, I can identify a valid analog.

---

### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook checks the `sender` argument against the allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension sees the router address as the swapper — not the actual user. A pool admin who allowlists the router (to permit router-mediated swaps for legitimate users) inadvertently opens the gate to every non-allowlisted user who routes through it.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension: [2](#0-1) 

The extension interface receives `sender` as the first argument of `beforeSwap`: [3](#0-2) 

The `SwapAllowlistExtension` is documented to key its allowlist lookup on `(pool, sender)`: [4](#0-3) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` — making `msg.sender` to the pool the **router contract address**, not the originating user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

Two broken outcomes follow:

1. **Bypass**: If the pool admin allowlists the router address (necessary for any legitimate user to swap via the router), every non-allowlisted user can bypass the restriction by routing through `MetricOmmSimpleRouter`.
2. **Lockout**: If the pool admin does not allowlist the router, all router-mediated swaps revert — including those from legitimately allowlisted users — breaking core swap functionality.

The analog to the BlueBerry H-4 bug is exact: just as `poke(token)` accrues interest for only the one token involved in the action while the liquidatability check silently uses stale values for all other tokens, the `beforeSwap` hook accrues the allowlist check against only the immediate `msg.sender` (the router) while the actual economic actor (the user) is never checked.

### Impact Explanation

A non-allowlisted user can trade on a curated, restricted pool by routing through the public `MetricOmmSimpleRouter`. This breaks the pool admin's access-control boundary without any privileged action. Depending on pool configuration (e.g., RWA pools, KYC-gated pools, or pools with restricted counterparties), this allows unauthorized principals to extract value or manipulate pool state in ways the admin explicitly intended to prevent. This qualifies as an admin-boundary break with direct fund-impacting consequences.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys a `SwapAllowlistExtension` and also expects users to swap via the router is affected. The router is a public, permissionless contract. No special setup beyond a standard router swap is required to trigger the bypass.

### Recommendation

The `SwapAllowlistExtension` should not check `sender` (the immediate pool caller). Instead, it should check the **originating user**. Two approaches:

1. Pass the true originating user through `extensionData` and verify it against a signature or trusted forwarder pattern.
2. Have the router forward the original `msg.sender` as part of `extensionData`, and have the extension decode and check that value — with the pool or factory enforcing that only trusted periphery contracts may supply this field.

The core pool should document that `sender` in extension hooks is the immediate `msg.sender` to the pool, not the end user, so extension authors do not conflate the two.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin adds Alice (address 0xA11CE) to the allowlist.
3. Pool admin adds the router address to the allowlist (required for Alice to swap via router).
4. Bob (address 0xB0B, not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: Bob})
5. Router calls pool.swap(recipient=Bob, ...) — msg.sender to pool = router.
6. _beforeSwap(sender=router, ...) is called.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (admin added router).
8. Hook passes. Bob's swap executes successfully despite not being allowlisted.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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

**File:** generate_scanned_questions.py (L719-724)
```python
            title="wrong-actor binding",
            question_focus="the hook checks the wrong actor among sender, owner, payer, or recipient",
            exploit="Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.",
            invariant="Every guard must key authorization to the same actor that the economic action is actually attributed to.",
            impact="High direct loss or policy bypass on curated pools.",
        ),
```
