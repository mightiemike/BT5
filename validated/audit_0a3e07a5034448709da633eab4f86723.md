### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the per-swapper allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address — not the actual end-user's address. If the router is allowlisted (required for any router-mediated swap to succeed), every user on the network can bypass the per-swapper allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` on the user's behalf. At that point `msg.sender` inside the pool is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

Two broken outcomes follow:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user on the network can bypass the per-user allowlist by routing through the router |
| Router **is not** allowlisted | Every individually-allowlisted user is silently blocked from using the router |

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the LP position owner, explicitly passed by the caller), not `sender`:

```solidity
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

The swap extension has no equivalent mechanism to recover the true end-user identity.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production access-control gate for pools that restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional desks, or protocol-owned routers). Bypassing it allows any unpermissioned address to execute swaps against a pool whose admin explicitly intended to restrict access. This breaks the core allowlist invariant and constitutes a broken core pool functionality / admin-boundary break with direct fund-impacting consequences (unauthorized parties drain liquidity at oracle prices from a restricted pool).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point documented in the periphery. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The router is a standard, deployed, trusted contract, so routing through it is a normal user action.

---

### Recommendation

The extension must check the **original end-user** identity, not the immediate caller of `pool.swap()`. Two viable fixes:

1. **Pass the true initiator through `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`, and the extension decodes and checks that value. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is that the economic beneficiary of the swap is the gated party, `recipient` (already available as the second argument to `beforeSwap`) is router-independent. However, `recipient` can also be set to an arbitrary address, so this only works if the allowlist semantics are "who receives output" rather than "who initiates the trade."

3. **Allowlist the router as a pass-through and gate inside the router**: Move the per-user check into the router itself, and only allowlist the router at the extension level. This centralizes trust in the router.

The cleanest fix matching the stated design intent ("gates `swap` by swapper address") is option 1.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for any router swap
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens

Result:
  - attacker, who was never allowlisted, successfully swaps on a restricted pool
  - The per-user allowlist is completely bypassed
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
