### Title
Missing Zero-Address Validation for `_token` in `Airdrop.initialize()` Enables Permanent Token Lock — (File: `core/contracts/Airdrop.sol`)

---

### Summary
`Airdrop.initialize()` accepts `_token` and `_sanctions` without zero-address validation. Because the function is `external` with only the `initializer` modifier and no access control, an unprivileged attacker can front-run the deployer's initialization call, set `token = address(0)`, and permanently brick all claim functionality while locking any airdrop tokens already deposited into the contract.

---

### Finding Description
`Airdrop.initialize()` is declared `external initializer` with no caller restriction: [1](#0-0) 

Both `_token` and `_sanctions` are stored directly without any `require(_token != address(0))` guard. The `token` state variable is written once and never updated afterward — there is no setter, no rescue function, and no re-initialization path.

The claim path unconditionally calls: [2](#0-1) 

If `token == address(0)`, `SafeERC20.safeTransfer(IERC20(address(0)), ...)` reverts on every call because there is no code at the zero address. Similarly, `_verifyProof` calls: [3](#0-2) 

If `sanctions == address(0)`, this external call also reverts, bricking proof verification entirely.

---

### Impact Explanation
- All airdrop recipients permanently lose their entitled tokens — `claim()` reverts for every caller.
- Any ERC-20 tokens already transferred into the `Airdrop` contract for distribution are irrecoverable: no `withdraw` or rescue function exists in the contract.
- `__Ownable_init()` sets `msg.sender` (the attacker) as owner, so the legitimate deployer cannot reclaim ownership or re-initialize. [4](#0-3) 

---

### Likelihood Explanation
Medium. The attack requires front-running a single pending `initialize()` transaction in the mempool, which is straightforward on any EVM chain without private mempools. The attacker needs no funds, no special role, and no prior state — only the ability to submit a transaction with higher gas before the deployer's transaction is mined. The griefing cost is negligible (one transaction), while the damage is permanent and protocol-wide for the airdrop component.

---

### Recommendation
Add explicit zero-address guards at the top of `initialize()`:

```solidity
function initialize(address _token, address _sanctions)
    external
    initializer
{
    require(_token != address(0), "Airdrop: token is zero address");
    require(_sanctions != address(0), "Airdrop: sanctions is zero address");
    __Ownable_init();
    token = _token;
    sanctions = _sanctions;
}
```

Additionally, deploy and initialize the proxy in a single atomic transaction (e.g., via a factory or deployment script that calls `initialize` in the same tx) to eliminate the front-running window entirely.

---

### Proof of Concept

1. Deployer broadcasts a transaction to call `Airdrop.initialize(validToken, validSanctions)`.
2. Attacker observes the pending transaction in the public mempool.
3. Attacker submits `Airdrop.initialize(address(0), address(0))` with higher gas — front-runs the deployer.
4. Attacker's call succeeds: `token = address(0)`, `sanctions = address(0)`, attacker is now `owner`.
5. Deployer's `initialize()` call reverts with `"Initializable: contract is already initialized"`.
6. Protocol transfers airdrop tokens to the `Airdrop` contract address.
7. Any user calls `claim(...)` → `_claim()` → `SafeERC20.safeTransfer(IERC20(address(0)), msg.sender, amount)` → **reverts** (no code at address 0).
8. Tokens are permanently locked; no recovery path exists. [5](#0-4)

### Citations

**File:** core/contracts/Airdrop.sol (L24-31)
```text
    function initialize(address _token, address _sanctions)
        external
        initializer
    {
        __Ownable_init();
        token = _token;
        sanctions = _sanctions;
    }
```

**File:** core/contracts/Airdrop.sol (L53-56)
```text
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
```

**File:** core/contracts/Airdrop.sol (L65-73)
```text
    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }
```
