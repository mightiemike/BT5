### Title
Unclaimed Airdrop Tokens Permanently Locked With No Recovery Mechanism — (`core/contracts/Airdrop.sol`)

---

### Summary

The `Airdrop` contract holds ERC20 tokens and distributes them exclusively through user-initiated `claim()` calls verified against Merkle proofs. There is no owner emergency-withdrawal function, no expiry mechanism, and no sweep path. Any tokens not claimed by eligible users are permanently locked in the contract with no on-chain recovery path.

---

### Finding Description

The `Airdrop` contract accumulates ERC20 tokens that are loaded externally and distributed via `claim()`. The only token outflow is `SafeERC20.safeTransfer` inside `_claim()`, which requires the caller to supply a valid Merkle proof for a registered week. [1](#0-0) 

The owner can register new Merkle roots via `registerMerkleRoot`, but there is no corresponding function to withdraw unclaimed tokens, recover tokens after a distribution window closes, or transfer the token balance in an emergency. [2](#0-1) 

The full contract surface is:

- `registerMerkleRoot` — owner-only, adds a new week's root
- `claim` — user-initiated, transfers tokens to the caller
- `getClaimed` — view only [3](#0-2) 

There is no `withdrawUnclaimed`, `sweep`, `recoverTokens`, or any equivalent function. The `OwnableUpgradeable` base provides no token-recovery capability. [4](#0-3) 

---

### Impact Explanation

Any ERC20 tokens loaded into the `Airdrop` contract that are not claimed by eligible users are permanently irrecoverable. Concretely:

- Tokens allocated to users who lose wallet access, abandon their accounts, or are later sanctioned (the contract checks sanctions at claim time, so a sanctioned user can never claim) are locked forever.
- Tokens allocated to addresses that were included in a Merkle root but never submitted a claim transaction are locked forever.
- There is no time-bounded expiry after which the protocol can reclaim undistributed supply.

The asset delta is the full unclaimed token balance of the contract — real ERC20 value with no recovery path.

---

### Likelihood Explanation

Airdrop non-participation rates are consistently high in practice (commonly 20–50% of eligible addresses never claim). Additionally, the sanctions check at claim time means any address that becomes sanctioned after a Merkle root is registered can never claim, and those tokens are silently locked. Both conditions are realistic and require no attacker action — they arise from ordinary user inactivity or regulatory events. [5](#0-4) 

---

### Recommendation

Add an owner-controlled recovery function with a time-lock or explicit "distribution closed" flag:

```solidity
function withdrawUnclaimed(address recipient) external onlyOwner {
    // optionally: require(block.timestamp > distributionDeadline)
    uint256 balance = IERC20(token).balanceOf(address(this));
    SafeERC20.safeTransfer(IERC20(token), recipient, balance);
}
```

This mirrors the recommended mitigation from the NukeFund report: give the owner a last-resort path to recover funds when the normal user-action-dependent distribution mechanism cannot drain the contract.

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root)` for week 1, and loads 1,000,000 tokens into the contract.
2. Only 600,000 tokens worth of eligible addresses ever call `claim()`.
3. The remaining 400,000 tokens sit in the contract.
4. No function exists to move them. `IERC20(token).balanceOf(address(airdrop))` returns 400,000 tokens indefinitely.
5. If any eligible address is sanctioned before claiming, their allocation is additionally unclaimable due to the sanctions check at line 53–56 of `Airdrop.sol`. [1](#0-0)

### Citations

**File:** core/contracts/Airdrop.sol (L11-31)
```text
contract Airdrop is OwnableUpgradeable, IAirdrop {
    address internal token;
    address internal sanctions;
    uint32 internal pastWeeks;

    mapping(uint32 => bytes32) internal merkleRoots;
    mapping(uint32 => mapping(address => uint256)) internal claimed;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address _token, address _sanctions)
        external
        initializer
    {
        __Ownable_init();
        token = _token;
        sanctions = _sanctions;
    }
```

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
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

**File:** core/contracts/Airdrop.sol (L75-96)
```text
    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }

    function getClaimed(address account)
        external
        view
        returns (uint256[] memory)
    {
        uint256[] memory result = new uint256[](pastWeeks + 1);
        for (uint32 week = 1; week <= pastWeeks; week++) {
            result[week] = claimed[week][account];
        }
        return result;
    }
}
```
