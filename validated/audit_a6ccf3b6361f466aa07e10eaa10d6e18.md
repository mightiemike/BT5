### Title
Unclaimed Airdrop Tokens Are Permanently Locked With No Recovery Mechanism — (File: `core/contracts/Airdrop.sol`)

---

### Summary

The `Airdrop.sol` contract distributes tokens weekly via Merkle-proof claims. It has no function to recover tokens that were allocated for a given week but never claimed. Any unclaimed allocation — whether due to user inaction, sanctioned addresses, or lost keys — is permanently locked in the contract with no path to recovery.

---

### Finding Description

The `Airdrop.sol` contract allows the owner to register a Merkle root per week via `registerMerkleRoot`, and users claim their allocation via `claim` → `_claim` → `_verifyProof`. The `_verifyProof` function enforces a sanctions check:

```solidity
require(
    !ISanctionsList(sanctions).isSanctioned(sender),
    "address is sanctioned."
);
``` [1](#0-0) 

Once a user is sanctioned or simply never claims, their allocation remains in the contract. The `claimed[week][sender]` mapping is only written when a claim succeeds:

```solidity
claimed[week][sender] = totalAmount;
``` [2](#0-1) 

There is no `sweep`, `recoverUnclaimed`, or equivalent function anywhere in the contract. The full contract surface is:

- `registerMerkleRoot` — owner only, registers a root
- `_verifyProof` / `_claim` / `claim` — user-facing claim path
- `getClaimed` — view only [3](#0-2) 

No code path exists to move tokens out of the contract except through a successful user claim. Tokens deposited for a week's distribution that are not fully claimed are irrecoverably locked.

---

### Impact Explanation

Any tokens deposited into `Airdrop.sol` for a given week that are not claimed are permanently locked. Concretely:

- A user who becomes sanctioned after the Merkle root is registered but before claiming loses their allocation forever.
- Any user who simply never claims (lost keys, inactivity) causes their share to be locked.
- The owner has no administrative escape hatch to recover these funds.

This is a direct asset loss: real ERC-20 tokens are transferred into the contract and can never leave it except through the claim path. The magnitude scales with the total unclaimed allocation across all weeks. [4](#0-3) 

---

### Likelihood Explanation

This is highly likely to occur in practice:

1. **Sanctions**: The contract explicitly checks a sanctions list at claim time. Any address sanctioned after a Merkle root is registered cannot claim, locking their allocation permanently.
2. **User inaction**: Airdrop recipients routinely fail to claim — lost wallets, forgotten allocations, or simply not interacting with the protocol.
3. **No time pressure**: Because there is no expiry on claims, unclaimed tokens accumulate indefinitely across all past weeks with no mechanism to recycle them. [5](#0-4) 

---

### Recommendation

Add an owner-controlled recovery function that can only be called after a sufficient lockup period (e.g., 90 days after a week's root was registered), allowing unclaimed tokens to be swept back to the treasury:

```solidity
function recoverUnclaimed(address recipient) external onlyOwner {
    // e.g., only callable after all claimable weeks have expired
    uint256 balance = IERC20(token).balanceOf(address(this));
    SafeERC20.safeTransfer(IERC20(token), recipient, balance);
}
```

This mirrors the recommended mitigation in M-03, which adds an admin function to claim remaining rewards after an epoch ends.

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root)` and deposits 10,000 NADO tokens into `Airdrop.sol` for week 1.
2. 8,000 tokens are claimed by active users.
3. Address `0xAlice` is added to the sanctions list before she claims her 500-token allocation.
4. Alice calls `claim(...)` → `_verifyProof` reverts with `"address is sanctioned."`.
5. The remaining 2,000 tokens (including Alice's 500) sit in the contract forever.
6. The owner has no function to recover them. `IERC20(token).balanceOf(address(Airdrop))` remains non-zero indefinitely. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/Airdrop.sol (L33-96)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }

    function _verifyProof(
        uint32 week,
        address sender,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        require(claimed[week][sender] == 0, "Already claimed.");
        require(
            merkleRoots[week] != bytes32(0),
            "Week hasn't been registered."
        );
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
        bytes32 leaf = keccak256(
            bytes.concat(keccak256(abi.encode(sender, totalAmount)))
        );
        bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
    }

    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }

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
