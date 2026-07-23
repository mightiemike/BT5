### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing non-allowlisted depositors to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller of `addLiquidity`, who provides tokens via the swap callback) and gates only on `owner` (the address that will own the resulting position). Because `owner` is a free caller-supplied argument, any non-allowlisted address can bypass the guard by setting `owner` to any allowlisted address it controls, while it is the non-allowlisted address that actually transfers tokens into the pool.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` (`sender`) is the entity that called `addLiquidity` and will be called back to transfer tokens into the pool. `owner` is a caller-supplied parameter that designates who will own the resulting liquidity position.

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to every registered extension:

```
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it with a blank identifier (discarding it entirely), then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The contract's own NatSpec states the intent is to gate **by depositor address**:

```
/// @notice Gates `addLiquidity` by depositor address, per pool.
``` [4](#0-3) 

The depositor is `sender` (the token-providing caller), not `owner`. The implementation checks the wrong address.

The `removeLiquidity` path correctly enforces `msg.sender == owner`:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [5](#0-4) 

This means the position is locked to `owner`, but the tokens that funded it came from the unchecked `sender`.

---

### Impact Explanation

An attacker who controls at least one allowlisted address `A` and any number of non-allowlisted addresses `B₁, B₂, …` can:

1. Call `pool.addLiquidity(owner = A, …)` from `Bₙ` (non-allowlisted).
2. The allowlist check evaluates `allowedDepositor[pool][A]` → passes.
3. `Bₙ` provides tokens via the liquidity callback.
4. The position is credited to `A`.
5. `A` calls `removeLiquidity` and returns the tokens to `Bₙ` off-chain.

The admin-configured deposit allowlist — the sole access-control boundary for liquidity provision — is fully bypassed for every non-allowlisted address that can coordinate with one allowlisted address. For pools deployed as permissioned venues (institutional, KYC-gated, or regulatory-compliance pools), this breaks the core invariant that only approved depositors can provide liquidity.

**Severity: Medium** — The guard is bypassed by a valid, unprivileged call path; no protocol-level funds are directly drained, but the allowlist invariant is broken and the pool's permissioned character is defeated.

---

### Likelihood Explanation

The attacker needs only one allowlisted address under their control — a realistic precondition since the attacker can legitimately obtain allowlist approval for one address and then use arbitrarily many non-approved addresses to deposit. The bypass requires no flash loans, no oracle manipulation, and no special timing; it is a single `addLiquidity` call with a crafted `owner` argument.

---

### Recommendation

Change the guard to check `sender` (the actual depositor) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the depositor and the position owner, both addresses should be checked. The `setAllowedToDeposit` management function and the `allowedDepositor` mapping key should remain keyed on the depositing address, consistent with the NatSpec. [6](#0-5) 

---

### Proof of Concept

```
// Setup: pool has DepositAllowlistExtension; attacker controls addressA (allowlisted) and addressB (not allowlisted)

// Step 1: pool admin allowlists addressA
depositAllowlist.setAllowedToDeposit(pool, addressA, true);

// Step 2: addressB (non-allowlisted) calls addLiquidity with owner = addressA
// beforeAddLiquidity receives: sender=addressB (ignored), owner=addressA (checked → passes)
vm.prank(addressB);
pool.addLiquidity(
    addressA,       // owner — allowlisted, check passes
    salt,
    deltas,
    callbackData,   // addressB provides tokens here
    extensionData
);

// Step 3: addressA removes liquidity and returns tokens to addressB off-chain
vm.prank(addressA);
pool.removeLiquidity(addressA, salt, deltas, extensionData);
// addressA transfers tokens back to addressB

// Result: addressB (non-allowlisted) has effectively deposited and withdrawn,
// bypassing the DepositAllowlistExtension entirely.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-11)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
