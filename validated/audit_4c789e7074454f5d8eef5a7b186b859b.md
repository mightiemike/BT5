The code is clear. Let me trace the exact path:

The full call chain is confirmed. Here is the determination:

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unapproved operator to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` calls to approved depositors. Its `beforeAddLiquidity` hook receives both `sender` (the actual `msg.sender` of `addLiquidity`) and `owner` (the position recipient) as separate parameters, but silently discards `sender` and checks only `owner`. Because `MetricOmmPool.addLiquidity` does not require `msg.sender == owner`, any unapproved operator can call `addLiquidity(approvedOwner, ...)` and pass the gate.

### Finding Description

`MetricOmmPool.addLiquidity` passes both the actual caller and the position owner to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both into the call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it `_` (unnamed, discarded) and checks only `owner`: [3](#0-2) 

The guard `allowedDepositor[msg.sender][owner]` (where `msg.sender` is the pool) checks whether the **position recipient** is approved, not whether the **actual caller** is approved. Since `addLiquidity` imposes no `msg.sender == owner` requirement (contrast with `removeLiquidity` which does): [4](#0-3) 

any unapproved operator can call `pool.addLiquidity(approvedOwner, ...)`, pay the tokens via callback, and the hook passes because `owner` is on the allowlist.

### Impact Explanation
The deposit allowlist — the sole access-control mechanism of `DepositAllowlistExtension` — is completely bypassed. A pool configured to accept deposits only from KYC'd or whitelisted addresses will accept deposits from any arbitrary caller, as long as they name an approved address as `owner`. This breaks the core functionality the extension exists to provide. The pool admin's restriction is rendered ineffective.

### Likelihood Explanation
The bypass requires only a direct call to `pool.addLiquidity(approvedOwner, ...)` from any EOA or contract. No privileged access, no special setup, no malicious token behavior is needed. The approved owner's address is public on-chain (readable from `allowedDepositor`). Likelihood is high.

### Recommendation
Replace the `owner` check with the `sender` argument (the first, currently unnamed parameter):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate on the position owner (KYC on position holders), the current `owner` check is correct but the extension should also enforce `sender == owner` or document that operator-on-behalf-of-owner is an accepted pattern.

### Proof of Concept

```solidity
// Foundry test sketch
function test_operatorBypassesAllowlist() public {
    // owner is allowlisted, operator is not
    depositExt.setAllowedToDeposit(address(pool), owner, true);

    // operator calls addLiquidity naming owner as position recipient
    vm.prank(operator); // operator NOT in allowedDepositor
    pool.addLiquidity(owner, 0, deltas, callbackData, "");
    // succeeds — allowlist gate is bypassed
}
``` [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-42)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
