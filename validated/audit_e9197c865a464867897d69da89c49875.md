### Title
Unclaimed ERC-20 Airdrop Tokens Are Permanently Locked With No Recovery Path — (`File: core/contracts/Airdrop.sol`)

---

### Summary

The `Airdrop` contract holds ERC-20 tokens for weekly distribution but exposes no owner withdrawal or token-recovery function. Any tokens not claimed by users — including unclaimed weekly allocations and any tokens accidentally sent to the contract — are permanently locked on-chain with no recovery mechanism.

---

### Finding Description

The `Airdrop` contract is initialized with a single `token` address and is funded with ERC-20 tokens for distribution to users. Users claim their allocations via `claim()` using Merkle proofs verified against per-week roots registered by the owner. [1](#0-0) 

The contract supports multiple weekly rounds tracked by `pastWeeks`, with each week having its own Merkle root and per-address claim record. [2](#0-1) 

The complete external/public interface of the contract is: `initialize`, `registerMerkleRoot`, `claim`, `getClaimed`. The `IAirdrop` interface confirms this surface. [3](#0-2) 

**There is no `withdraw`, `recoverTokens`, `sweep`, or equivalent function anywhere in the contract.** The owner has no on-chain path to recover:

- Tokens loaded for a week where some users never claim their allocation
- Tokens loaded in excess of the actual Merkle tree total
- Any other ERC-20 tokens accidentally transferred to the contract address

The `claim` function only transfers tokens outward to claimants: [4](#0-3) 

No inverse path exists. The `claimed` mapping records what each address has claimed per week, but there is no on-chain accounting of how many tokens were loaded per week, and no function to drain the residual balance. [5](#0-4) 

---

### Impact Explanation

Any ERC-20 tokens held by the `Airdrop` contract that are not claimed by users are permanently locked. The protocol cannot recover them. This is a direct, quantifiable financial loss: the difference between tokens loaded into the contract and tokens actually claimed is irrecoverable. Over multiple weekly rounds, this residual accumulates. The `token` state variable is set once at initialization and cannot be changed, so even a redeployment of the contract does not help recover tokens already locked in the existing instance. [6](#0-5) 

---

### Likelihood Explanation

**High.** Airdrop campaigns routinely have unclaimed allocations — users lose private keys, forget to claim, are sanctioned mid-campaign, or simply never interact with the contract. The `Airdrop` contract explicitly supports multi-week campaigns (`pastWeeks` increments with each `registerMerkleRoot` call), meaning unclaimed tokens accumulate across every round. The sanctioned-address check in `_verifyProof` further guarantees that some Merkle-leaf allocations can never be claimed. [7](#0-6) [8](#0-7) 

---

### Recommendation

Add an owner-only token recovery function to `Airdrop.sol`:

```solidity
function recoverTokens(address tokenAddr, uint256 amount, address to) external onlyOwner {
    SafeERC20.safeTransfer(IERC20(tokenAddr), to, amount);
}
```

This mirrors the pattern already used in `DirectDepositV1.sol`, which provides both `withdraw(IIERC20Base token)` and `withdrawNative()` for the same reason. [9](#0-8) 

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, merkleRoot)` and transfers 1,000,000 NADO tokens into the `Airdrop` contract to fund week 1.
2. Over the week, users claim 750,000 tokens. 50,000 tokens belong to sanctioned addresses (blocked by `isSanctioned` check) and 200,000 belong to users who never interact.
3. Week 1 ends. Owner calls `registerMerkleRoot(2, newRoot)` to start week 2.
4. The 250,000 unclaimed tokens from week 1 remain in the contract indefinitely.
5. Owner attempts to recover them — no function exists. The tokens are permanently locked.
6. This repeats every week, compounding the loss. [10](#0-9)

### Citations

**File:** core/contracts/Airdrop.sol (L11-96)
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

**File:** core/contracts/interfaces/IAirdrop.sol (L1-19)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

interface IAirdrop {
    event Claim(address indexed account, uint32 week, uint256 amount);

    struct ClaimProof {
        uint32 week;
        uint256 totalAmount;
        bytes32[] proof;
    }

    function claim(ClaimProof[] calldata claimProofs) external;

    function getClaimed(address account)
        external
        view
        returns (uint256[] memory);
}
```

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```
