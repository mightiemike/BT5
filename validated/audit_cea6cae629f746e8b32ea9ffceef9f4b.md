### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address against the allowlist instead of the originating EOA. A pool admin who allowlists the router (a natural configuration to support router-mediated swaps) inadvertently opens the pool to every user who can call the router, defeating the per-user curation the allowlist was meant to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput` (or any router entry point), the router calls `pool.swap(...)` with itself as `msg.sender`. The pool therefore passes the **router's address** as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalEOA]`.

This creates an impossible configuration for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user who can call the router bypasses the individual allowlist |

The second branch is the bypass. A pool admin who wants to support router-mediated swaps for their curated pool will naturally add the router to the allowlist. At that point, any unprivileged user can call `MetricOmmSimpleRouter` and swap on the pool, regardless of whether they are individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it gates the `owner` (position holder), which is explicitly passed by the caller and is the economically correct actor to gate for deposits. [4](#0-3) [3](#0-2) 

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only, or regulatory-restricted) that configures `SwapAllowlistExtension` and allowlists the public router loses its access control entirely. Any user can execute swaps against the pool's LP liquidity, causing:

- **Direct LP fund loss**: unauthorized swaps drain LP positions at oracle-quoted prices.
- **Broken core pool functionality**: the pool's curation invariant — that only approved counterparties trade against LP capital — is violated.
- **Pool insolvency risk**: if the pool is designed for a specific counterparty set (e.g., hedging a specific portfolio), unrestricted swaps can leave LPs with unintended exposure.

This matches the allowed impact gate: broken core pool functionality causing loss of funds, and admin-boundary break where an unprivileged path bypasses a configured guard.

---

### Likelihood Explanation

**Medium.** The precondition is that the pool admin allowlists the router. This is a natural and expected configuration — the router is the protocol's own periphery contract and the primary user-facing entry point. A pool admin who wants to support router-mediated swaps for their allowlisted users has no way to do so without also opening the pool to all router users. The extension's design makes the correct configuration unreachable, so the bypass is reachable through a reasonable admin action, not a malicious one.

---

### Recommendation

Pass the original transaction initiator (`tx.origin`) or require the router to forward the originating user address through `extensionData` so the extension can check the real actor. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the factory level (e.g., reject pools that configure both a swap allowlist and a non-zero router address). A cleaner fix is to have the router pass the original caller in a standardized field of `extensionData` and have the extension decode it, with the pool verifying the router's identity before trusting the forwarded address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (not individually allowlisted) calls
     MetricOmmSimpleRouter.exactInput(..., pool, ...).
  2. Router calls pool.swap(recipient, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(router, recipient, ...).
  4. Extension evaluates allowedSwapper[pool][router] → true → passes.
  5. Swap executes. Attacker receives output tokens from LP liquidity.

Result:
  - Attacker swapped on a pool they are not individually allowlisted for.
  - LP funds were transferred to an unauthorized counterparty.
  - The swap allowlist guard was bypassed through the public router path.
``` [3](#0-2) [2](#0-1) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
