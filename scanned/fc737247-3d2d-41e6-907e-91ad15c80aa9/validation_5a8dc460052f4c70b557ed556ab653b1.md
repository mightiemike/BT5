### Title
SwapAllowlistExtension Gates Router Address Instead of End-User Sender, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist lookup is keyed by `(pool, sender)`, so it evaluates the router's allowlist status rather than the actual trader's. This makes the per-user swap allowlist unenforceable for any router-mediated swap path.

---

### Finding Description

**Root cause in `MetricOmmPool.swap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this `sender` verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
``` [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` performs its allowlist lookup keyed by `(pool, sender)` — i.e., `allowedSwapper[pool][sender]`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. [3](#0-2) 

**The identity mismatch:**

| Call path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User → Pool directly | User address | Correct |
| User → Router → Pool | Router address | Wrong — checks router, not user |

The pool admin cannot simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from using the router, because the extension cannot distinguish between the two cases — it only sees the router as `sender`.

**Two concrete failure modes:**

1. **Bypass (higher impact):** If the pool admin adds the router to the allowlist (or sets `allowAll = true` for the router), every user — including explicitly non-allowlisted addresses — can swap by routing through `MetricOmmSimpleRouter`. The per-user allowlist is completely defeated.

2. **Lockout (lower impact):** If the router is not in the allowlist, every allowlisted user who routes through the router is blocked, even though they are individually permitted. The router becomes unusable for allowlisted pools.

---

### Impact Explanation

The `SwapAllowlistExtension` is the primary mechanism for pool admins to restrict swap access to a curated set of counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-controlled addresses). When the allowlist check evaluates the router's identity instead of the end user's identity, the access control invariant is broken:

- **Allowlist bypass:** An unprivileged, non-allowlisted address can execute swaps in a restricted pool by routing through the public `MetricOmmSimpleRouter`. This is an admin-boundary break — an access control configured by the pool admin is bypassed by an unprivileged path.
- **Fund impact:** If the pool is restricted to protect LPs from adversarial counterparties (e.g., informed traders, sanctioned addresses), the bypass allows those counterparties to trade against LP capital, causing LP principal loss.

This matches the allowed impact gate: **Admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path** and **Broken core pool functionality**.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, publicly deployed user-facing entry point for swaps. Any user who interacts with the protocol through the normal UI or SDK will route through it.
- No special privileges, flash loans, or unusual token behavior are required. Any address can call `MetricOmmSimpleRouter.exactInput` or `exactOutput`.
- The bypass is deterministic and repeatable on every block.
- Pool admins who deploy `SwapAllowlistExtension` to restrict access have no on-chain mechanism to prevent router-mediated bypass without also blocking all router users.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor — the end user — not the intermediary router. Two approaches:

1. **Check `recipient` instead of `sender`:** For swap allowlists, the `recipient` is often the end user. However, `recipient` can be set to any address by the router caller, so this is also gameable.

2. **Require the pool to be called directly (no router intermediary):** Add a check that `sender == recipient` or that `sender` is not a known router. This is fragile.

3. **Pass the original caller through `extensionData`:** The router should encode the actual end-user address in `extensionData`, and the extension should decode and check it. This requires the extension to trust the router's self-reported caller, which introduces a different trust assumption.

4. **Preferred — check `sender` and require direct pool interaction for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not configure `MetricOmmSimpleRouter` as an allowed intermediary, and the extension should revert if `sender` is a known router address unless the router itself is the intended gated entity.

The cleanest fix is for `SwapAllowlistExtension.beforeSwap` to check `recipient` when `sender` is a known router, or to require that `extensionData` carries a signed end-user identity when routing through intermediaries.

---

### Proof of Concept

```solidity
function testSwapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, alice is allowlisted, bob is not
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    // Pool admin allowlists alice only
    swapAllowlistExtension.setAllowedSwapper(address(pool), alice, true);
    // Router is also allowlisted so allowlisted users can use it
    swapAllowlistExtension.setAllowedSwapper(address(pool), address(router), true);

    // Alice can swap directly — expected
    vm.prank(alice);
    pool.swap(alice, false, 1000, type(uint128).max, "", "");

    // Bob is NOT allowlisted — direct swap should revert
    vm.prank(bob);
    vm.expectRevert(); // NotAllowedToSwap
    pool.swap(bob, false, 1000, type(uint128).max, "", "");

    // Bob routes through the public router
    // The pool sees msg.sender = router (allowlisted), not bob
    // The extension checks allowedSwapper[pool][router] = true → passes
    vm.prank(bob);
    // This succeeds — bob bypasses the allowlist via the router
    router.exactInput(
        IMetricOmmSimpleRouter.ExactInputParams({
            pool: address(pool),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: bob,
            extensionData: ""
        })
    );
    // bob received tokens despite not being allowlisted
}
```

The `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the check passes. Bob's swap settles successfully despite being explicitly excluded from the allowlist. [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
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
