### Title
`SwapAllowlistExtension` gates on the router's address instead of the end-user identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender`. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end user. An admin who allowlists the router to enable legitimate router-mediated swaps for authorized users inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router, so the extension receives `sender = router` and checks `allowedSwapper[pool][router]` — not the end user's address.

The admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Authorized users cannot use the router at all |
| **Allowlist the router** | Every user can bypass the allowlist through the router |

There is no configuration that simultaneously allows authorized users to route through `MetricOmmSimpleRouter` and blocks unauthorized users from doing the same.

The `ExtensionCalling._beforeSwap` dispatcher confirms the binding — `sender` is always `msg.sender` of the originating `swap()` call: [3](#0-2) 

The `SwapAllowlistExtension` interface documents the intent as gating "by swapper address, per pool," but the checked address is the intermediary contract, not the swapper: [4](#0-3) 

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is the only mechanism preventing unauthorized counterparties from trading against the pool's liquidity. Bypassing it allows unauthorized users to execute swaps at oracle-anchored prices, extracting value from LPs in a pool that was deliberately restricted to prevent exactly that. This is a direct loss of LP principal.

---

### Likelihood Explanation

Medium. The bypass requires the admin to allowlist the router — a natural and necessary configuration step if any authorized user is expected to use the router. The admin has no way to know that this step opens the pool to all users; the allowlist UI and documentation present it as a per-address control. Once the router is allowlisted (even for one authorized user), the bypass is permanently available to everyone.

---

### Recommendation

The extension must verify the end user's identity, not the intermediary's. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes the originating user's address into `extensionData`; the extension decodes and verifies it against the allowlist. The pool already forwards `extensionData` unmodified to every hook.
2. **Separate router-level allowlist**: Deploy a router wrapper that enforces its own per-user allowlist before calling `pool.swap()`, and allowlist only that wrapper in the extension.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Admin allowlists Alice: `extension.setAllowedToSwap(pool, alice, true)`.
3. Admin also allowlists the router so Alice can use it: `extension.setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInput(pool, ...)`.
5. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against the restricted pool, bypassing the allowlist. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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
