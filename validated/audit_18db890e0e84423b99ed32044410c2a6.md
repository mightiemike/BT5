### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. If the transfer silently returns `false` instead of reverting, the function continues to withdraw usdcE from the target DDA and send it to the caller — effectively allowing an unprivileged caller to drain usdcE from any DDA without providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is an externally callable function (no access control modifier) that is intended to atomically swap usdcE held in a `DirectDepositV1` (DDA) contract for USDC provided by the caller:

1. Read `balance` of usdcE in the DDA.
2. Pull `balance` USDC from `msg.sender` into the DDA via `transferFrom`.
3. Withdraw usdcE from the DDA to `ContractOwner` via `DirectDepositV1.withdraw`.
4. Forward the usdcE to `msg.sender` via `safeTransfer`.

The critical flaw is at step 2:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value is silently discarded. [1](#0-0) 

Steps 3 and 4 execute unconditionally regardless of whether step 2 succeeded:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`, and some ERC20 implementations return `false` on failure rather than reverting. [3](#0-2) 

Contrast this with every other transfer in the codebase, which uses `ERC20Helper.safeTransferFrom` — a wrapper that explicitly requires `success && (data.length == 0 || abi.decode(data, (bool)))`. [4](#0-3) 

The function has no access control — any external address can call it on any subaccount that has a registered DDA with a non-zero usdcE balance. [5](#0-4) 

---

### Impact Explanation

If the USDC `transferFrom` returns `false` (e.g., caller has zero USDC allowance or zero balance, and the token implementation returns `false` rather than reverting), the caller receives the full usdcE balance of the target DDA at zero cost. The DDA's usdcE is permanently drained. This is a direct asset theft: the attacker gains usdcE tokens and the DDA owner loses them, with no compensation.

---

### Likelihood Explanation

The function is permissionlessly callable by any address on chain ID 57073 (Ink). The USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink is a bridged/wrapped variant whose exact revert-vs-return-false behavior on insufficient allowance must be verified. If it returns `false` on failure (as some non-Circle USDC variants do), the exploit is immediately executable. Even if the current token reverts, the code is structurally broken: a future token upgrade or a different token registered at that address could silently fail, triggering the same drain.

---

### Recommendation

Replace the bare `transferFrom` call with the project's own `ERC20Helper.safeTransferFrom`, which already exists and is used everywhere else in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [6](#0-5) 

---

### Proof of Concept

1. Identify a subaccount whose DDA holds a non-zero usdcE balance on chain 57073.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from an address with zero USDC balance or zero USDC allowance to `ContractOwner`.
3. If USDC `transferFrom` returns `false` instead of reverting: no USDC leaves the attacker's wallet.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes — usdcE moves from DDA → ContractOwner.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` executes — usdcE moves from ContractOwner → attacker.
6. Attacker holds the full usdcE balance; DDA is drained; no USDC was ever provided. [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
