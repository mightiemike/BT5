### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (the natural setup for a curated pool that supports router-mediated swaps), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap()` then checks:

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

Here `msg.sender` is the pool (correct, used as the mapping key), and `sender` is the address the pool received as its own `msg.sender`. When a user calls `MetricOmmSimpleRouter.exactInput()`, the router calls `pool.swap()`, so the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router address**, not the end user.

A pool admin who wants to support router-mediated swaps will allowlist the router:

```solidity
swapExtension.setAllowedToSwap(address(pool), address(router), true);
```

From that point on, `allowedSwapper[pool][router] == true`, so `beforeSwap` passes for every call that arrives through the router, regardless of who the actual end user is. The per-user allowlist is completely bypassed.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the explicit position owner passed to `addLiquidity`), which the `MetricOmmPoolLiquidityAdder` forwards correctly as the real depositor.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unpermissioned address can execute swaps against the pool's liquidity, draining LP value at oracle prices without the access control the pool admin intended to enforce.

---

### Likelihood Explanation

**Medium.** The router is the standard user-facing entrypoint documented and tested by the protocol. Pool admins who configure a swap allowlist and also want to support the router will naturally allowlist the router address. The bypass requires no special privileges — any EOA can call the public router.

---

### Recommendation

The extension must check the **end user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` accept a `swapper` parameter and pass it as the `recipient` or via `extensionData`, then have the extension read the real user from there. This requires a protocol-level convention.

2. **Check `recipient` instead of `sender` when the sender is a known router**, or — more robustly — require that the pool's `swap()` accept an explicit `swapper` argument (separate from `recipient`) that is forwarded to extensions as the identity to gate.

The minimal safe fix for the extension itself is to reject any `sender` that is not itself an EOA or a known allowlisted address, but the cleanest solution is to thread the real user identity through the call stack so the extension always sees the economically relevant actor.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (router is allowlisted so that normal users can trade via the router)
  - alice is NOT in allowedSwapper[pool]

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInput(...)
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, callbackData, extensionData)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  ✓  → passes
  6. Swap executes against pool liquidity with alice as the economic beneficiary

Result: alice, a non-allowlisted address, successfully swaps on a curated pool.
```

**Relevant code locations:**

- Pool passes `msg.sender` as `sender`: [1](#0-0) 
- Extension checks `sender` (the router, not the end user): [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged: [3](#0-2) 
- Allowlist mapping keyed by `[pool][swapper]`: [4](#0-3) 
- `DepositAllowlistExtension` correctly checks `owner` (not `sender`), showing the asymmetry: [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
