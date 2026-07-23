### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end user. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user on the network the ability to swap, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (enforced by `onlyPool` in `BaseMetricExtension`) and `sender` is the first argument the pool passes to the hook — which is `msg.sender` of the `pool.swap()` call itself. [2](#0-1) 

The pool's `_beforeSwap` dispatcher forwards this value verbatim: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)` on the user's behalf. At that point `msg.sender` seen by the pool — and therefore the `sender` forwarded to the extension — is the **router address**, not the originating user. The extension has no access to the true end user.

The pool admin now faces an inescapable dilemma:

| Configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | **Every** user on the network can bypass the allowlist via the router |

Because `MetricOmmSimpleRouter` is a public, permissionless contract, allowlisting it is equivalent to disabling the allowlist for swaps. [4](#0-3) 

The `DepositAllowlistExtension` does **not** share this flaw — it gates on `owner` (the position beneficiary), not on the unnamed first `sender` parameter, so the operator/payer separation is intentional and correct there. [5](#0-4) 

---

### Impact Explanation

Any user can swap in a pool that the admin intended to restrict to a specific allowlist by routing through the public `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension. Pools designed for permissioned counterparties (e.g., institutional or compliance-gated pools) are fully open to arbitrary swappers. LPs in such pools may suffer direct principal loss if the pool's economics depend on only trusted counterparties trading against the oracle-anchored bins.

**Severity: High** — allowlist bypass is complete and unconditional once the router is allowlisted; no privileged action by the attacker is required.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, deployed periphery contract.
- Any user can call it with no special permissions.
- The bypass requires only a standard router call — no flash loans, callbacks, or admin access.
- Pool admins are likely to allowlist the router to support normal UX, making the bypass reachable in every realistic deployment. [6](#0-5) 

---

### Recommendation

Pass the **originating user** through the call chain rather than the immediate caller. Two viable approaches:

1. **Decode the true sender from `extensionData`**: The router encodes the original `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires the router to be trusted to set this field honestly (acceptable given it is a protocol-owned contract).

2. **Check `sender` against a router registry and then verify the user via a separate callback or transient storage slot**: The extension detects that `sender` is the router and reads the actual user from a transient context the router wrote before calling the pool.

Either way, the extension must gate on the **economic actor** (the user whose funds are at risk), not the **proximate caller** (the router).

---

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — necessary so that allowlisted users can use the router.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
     targeting the restricted pool.
  5. Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
  6. Pool calls _beforeSwap(router, recipient, ...).
  7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
  8. Swap executes. Attacker swaps successfully in a pool they were never
     authorized to access.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
``` [4](#0-3) [3](#0-2) [2](#0-1)

### Citations

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
