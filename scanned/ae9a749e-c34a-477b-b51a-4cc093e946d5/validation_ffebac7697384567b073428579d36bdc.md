### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate for every user, defeating the per-address allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension` stores per-pool, per-address permissions:

```solidity
mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
mapping(address pool => bool) public allowAllSwappers;
``` [1](#0-0) 

Its `beforeSwap` hook enforces the gate:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool forwarded as the swap initiator. The pool assembles this call inside `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(
    address sender,
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [3](#0-2) 

The pool's `swap` entry-point uses `msg.sender` as the `sender` it forwards. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension receives `sender = router`. The allowlist lookup becomes:

```
allowedSwapper[pool][router]   ← checked
allowedSwapper[pool][Alice]    ← never checked
```

A pool admin who wants router-mediated swaps to work **must** allowlist the router. The moment the router is allowlisted, every user — including those the admin explicitly never added — can execute swaps by going through the router. The per-address allowlist collapses to a binary "router allowed / router blocked" flag, identical to `allowAllSwappers`.

The protocol's own research notes confirm this is the intended investigation surface:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*
> *"Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market-makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The unauthorized swapper can drain arbitrage value from the pool, execute swaps at oracle-anchored prices the pool admin reserved for specific parties, or interact with the pool in ways that violate the pool's risk model. This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized price-taking against LP capital).

---

### Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user aware of the allowlist restriction can trivially route through the router instead of calling the pool directly. No privileged access, special tokens, or unusual conditions are required — only knowledge of the router address and a standard `exactInputSingle` call.

---

### Recommendation

The `sender` identity forwarded to extensions must reflect the **economic actor**, not the intermediary contract. Two complementary fixes:

1. **Pool-level**: Have `MetricOmmPool.swap` accept an explicit `sender` parameter (the true initiator) rather than using `msg.sender`, and require the router to pass `msg.sender` (the user) as that argument. This mirrors how `addLiquidity` already separates `sender` from `owner`.

2. **Extension-level fallback**: `SwapAllowlistExtension` could accept an `extensionData`-encoded address as the true swapper and verify it against a signature or trusted forwarder registry, but this is more complex and error-prone.

The cleanest fix is option 1: align the pool's `sender` semantics with the actual user, so the allowlist always gates the address that benefits from the swap.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension (allowAllSwappers = false).
2. Pool admin calls setAllowedToSwap(pool, router, true)   ← must do this for router to work
3. Pool admin does NOT call setAllowedToSwap(pool, Alice, true)

Attack
──────
4. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=Alice, ...)
6. Pool sets sender = msg.sender = router, calls _beforeSwap(router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Alice's swap executes at oracle price against LP capital.

Expected: revert NotAllowedToSwap (Alice is not allowlisted)
Actual:   swap succeeds — Alice bypassed the allowlist via the router
```

The `SwapAllowlistExtension.beforeSwap` check at line 37 never evaluates Alice's address; it only ever sees the router. [5](#0-4)

### Citations

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
